"""Прогон лестницы по списку доменов из файла, без базы.

Зачем отдельный путь. Обычный поиск (`search.py`) идёт по своей очереди
в базе: он знает про доноров, рекламодателей и их исходы. Иногда на входе
просто список чужих доменов — чей-то экспорт, чужая база, разбор отчёта, —
и заводить их донорами, чтобы обойти, значит засорить свою базу данными,
которые к ней не относятся.

Поэтому здесь: CSV со списком доменов на входе, CSV с контактами на выходе,
ни одной записи в базу. Инструмент ничего не знает о том, откуда список.

**Итог собирается из чекпойнта, а не из памяти.** Прогон по нескольким
тысячам доменов идёт часами и обрывается: связь, потолок файлов, Ctrl-C.
Каждый домен дописывается строкой в JSONL сразу, повторный запуск
пропускает уже пройденные, а CSV печатается из файла — поэтому он полный
и после обрыва, и после продолжения. Собери его из памяти процесса, и
продолженный прогон отдал бы только вторую половину.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.config import contacts as cfg
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.messengers import MessengerKind, Trust
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)

#: Сколько доменов проходим одновременно. То же число, что у поиска по базе:
#: больше — упрёмся в вежливость к чужим сайтам, меньше — прогон растянется.
CONCURRENCY = 8

#: Как часто сообщать о ходе дела. Прогон на часы без вывода читается
#: как зависший.
PROGRESS_EVERY = 25

#: Имена колонок, в которых может лежать домен. Порядок — порядок доверия.
HOST_COLUMNS = ("host_key", "host", "domain", "site", "site_url", "url", "website")

#: Колонки итога. Каналы — по одному столбцу на вид, значения через `; `.
OUTPUT_COLUMNS = (
    "host", "status", "email", "emails", "email_source", "email_page", "has_form",
    "telegram", "skype", "whatsapp", "viber", "vk", "phone",
    "guessed", "handles_page", "rejected",
)  # fmt: skip


@dataclass(slots=True)
class SweepReport:
    """Чем кончился прогон."""

    total: int = 0
    walked: int = 0
    skipped: int = 0
    with_email: int = 0
    with_handle: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def with_any_contact(self) -> int:
        return self.with_email + self.with_handle


def host_from_cell(raw: str) -> str:
    """`https://WWW.News.example.com/path` → `news.example.com`.

    Поддомен НЕ срезается, в отличие от `donors.host.normalize_host`:
    тот сводит домен к ключу дедупликации, а нам нужен адрес, по которому
    идти. `news.example.com` и `example.com` — разные сайты, и обойти надо
    тот, что дали на входе.
    """
    candidate = (raw or "").strip().lower()
    if not candidate:
        return ""
    if "//" not in candidate:
        candidate = f"//{candidate}"
    host = urlsplit(candidate).hostname or ""
    return host.removeprefix("www.").rstrip(".")


def _pick_column(fieldnames: Sequence[str], wanted: str | None) -> str:
    known = {name.strip().lower(): name for name in fieldnames if name}
    if wanted:
        if wanted.strip().lower() not in known:
            raise ValueError(f"в файле нет колонки {wanted!r}; есть: {', '.join(known.values())}")
        return known[wanted.strip().lower()]
    for name in HOST_COLUMNS:
        if name in known:
            return known[name]
    raise ValueError(
        "не нашлось колонки с доменом. Ожидаются "
        f"{', '.join(HOST_COLUMNS)} — или назовите её сами. В файле: {', '.join(known.values())}"
    )


def read_hosts(path: Path, *, column: str | None = None, delimiter: str | None = None) -> list[str]:
    """Домены из CSV, по одному на строку, без повторов и в порядке файла.

    Разделитель угадывается по первой строке: наш экспорт пишет `;`, чужой
    обычно `,`. Угадывание ошибается на файле из одной колонки без
    разделителей вовсе — тогда его называют явно.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        first = handle.readline()
        handle.seek(0)
        sep = delimiter or (";" if first.count(";") > first.count(",") else ",")
        reader = csv.DictReader(handle, delimiter=sep)
        if not reader.fieldnames:
            return []
        key = _pick_column(reader.fieldnames, column)
        seen: dict[str, None] = {}
        for row in reader:
            host = host_from_cell(row.get(key) or "")
            if host:
                seen.setdefault(host, None)
    return list(seen)


def done_hosts(checkpoint: Path) -> set[str]:
    """Домены, уже записанные в чекпойнт.

    Битая строка (прогон убили на середине записи) пропускается молча,
    но именно она и только она: домен просто пройдут заново.
    """
    if not checkpoint.exists():
        return set()
    hosts: set[str] = set()
    with checkpoint.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                hosts.add(str(json.loads(line)["host"]))
            except (ValueError, KeyError, TypeError):
                logger.info("чекпойнт: строка не разобралась, домен пройдём заново")
    return hosts


def _handle_columns(result: LadderResult) -> dict[str, str]:
    """Каналы по видам. Догадки — отдельной колонкой, и это важно.

    Явный `t.me/nick` со страницы и ник, угаданный из текста, — разного
    качества: `@nick` в подвале бывает аккаунтом в другой сети или просто
    обращением. Смешав их в одной колонке, мы получили бы рассылку по
    чужим людям при первой же выгрузке «всё, что нашли».

    Один и тот же канал сводится в одну запись, хотя приходит по многу
    раз: подвал с телеграмом стоит на каждой странице сайта, и без этого
    колонка выглядела бы как «adsdesk; adsdesk; adsdesk». Если канал
    где-то назван ссылкой, а где-то только словами, он считается явным:
    одного прямого упоминания довольно.
    """
    trusted: dict[tuple[str, str], bool] = {}
    for handle in result.handles:
        key = (handle.kind.value, handle.value)
        trusted[key] = trusted.get(key, False) or handle.trust is Trust.EXPLICIT

    explicit: dict[str, list[str]] = {kind.value: [] for kind in MessengerKind}
    guessed: list[str] = []
    for (kind, value), is_explicit in sorted(trusted.items()):
        if is_explicit:
            explicit[kind].append(value)
        else:
            guessed.append(f"{kind}:{value}")

    columns = {kind: "; ".join(values) for kind, values in explicit.items()}
    columns["guessed"] = "; ".join(guessed)
    pages = {handle.page_url for handle in result.handles if handle.page_url}
    columns["handles_page"] = "; ".join(sorted(pages))
    return columns


def row_of(result: LadderResult) -> dict[str, str]:
    """Строка итога по одному домену. Пустые поля — законный исход."""
    emails = sorted({candidate.email for candidate in result.candidates})
    row = {
        "host": result.host,
        "status": result.status.value,
        "email": result.contact.email if result.contact else "",
        "emails": "; ".join(emails),
        "email_source": result.source.value if result.source else "",
        "email_page": (result.contact.page_url or "") if result.contact else "",
        "has_form": "true" if result.has_form else "",
        "rejected": "; ".join(f"{email} ({reason})" for email, reason in result.rejected),
    }
    row.update(_handle_columns(result))
    return row


def write_csv(rows: Iterable[dict[str, str]], path: Path) -> int:
    """Собрать итоговый CSV. UTF-8 с BOM — чтобы Excel открыл его щелчком."""
    written = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS), delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in OUTPUT_COLUMNS})
            written += 1
    return written


def rows_from_checkpoint(checkpoint: Path) -> list[dict[str, str]]:
    """Строки итога из чекпойнта, в порядке прохода."""
    if not checkpoint.exists():
        return []
    rows: list[dict[str, str]] = []
    with checkpoint.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(dict(json.loads(line)["row"]))
            except (ValueError, KeyError, TypeError) as exc:
                # Обрыв посреди записи оставляет последнюю строку недописанной:
                # пропустить её законно, а молча — нет, иначе итог, который
                # короче прохода на домен, нечем объяснить.
                logger.warning(
                    "контакты: строка %s чекпойнта %s не разобрана (%r) — пропущена",
                    number,
                    checkpoint,
                    exc,
                )
    return rows


class _Checkpoint:
    """Дописывает по строке на домен и сбрасывает на диск сразу.

    Без сброса строки живут в буфере, и обрыв съедает последние сотни
    доменов — то есть именно то, от чего чекпойнт защищает.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def add(self, host: str, row: dict[str, str]) -> None:
        line = json.dumps({"host": host, "row": row}, ensure_ascii=False)
        async with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()


async def sweep(
    hosts: Sequence[str],
    *,
    checkpoint: Path,
    use_browser: bool = False,
    concurrency: int = CONCURRENCY,
    on_progress: Callable[[int, int], None] | None = None,
) -> SweepReport:
    """Обойти домены и дописать исход каждого в чекпойнт.

    Домен без почтового обслуживания всё равно обходится
    (`stop_without_mail=False`): MX-проверка отвечает на вопрос «дойдёт ли
    письмо», а телеграм в подвале от неё не зависит. У поиска по базе
    поведение прежнее — там ищут адрес, и ради него ступени не стоит
    тратить на домен без почты.
    """
    report = SweepReport(total=len(hosts))
    if not hosts:
        return report

    keeper = _Checkpoint(checkpoint)
    limiter = asyncio.Semaphore(max(1, concurrency))
    timeout = httpx.Timeout(cfg.PAGE_TIMEOUT_SEC, connect=cfg.PAGE_TIMEOUT_SEC)

    async with guarded_client(timeout=timeout) as http, PlaywrightRenderer() as renderer:
        ladder = ContactLadder(
            http,
            provider=None,  # платных ступеней здесь нет вовсе
            renderer=renderer if use_browser else None,
            stop_without_mail=False,
            collect_handles=True,  # каналы связи — ради них прогон по файлу и нужен
        )

        async def one(host: str) -> None:
            async with limiter:
                try:
                    result = await ladder.find(host)
                except Exception:
                    # Падение на одном домене не должно стоить прогона, но
                    # и молчать о нём нельзя: без строки в чекпойнте домен
                    # вернётся в следующий запуск, и это правильный исход.
                    logger.exception("прогон: домен %s не обошёлся", host)
                    return
                row = row_of(result)
                await keeper.add(host, row)
                report.walked += 1
                report.with_email += bool(row["email"])
                report.with_handle += bool(not row["email"] and result.handles)
                if on_progress is not None and report.walked % PROGRESS_EVERY == 0:
                    on_progress(report.walked, report.total)

        await asyncio.gather(*(one(host) for host in hosts))
        report.counters = ladder.counters.as_report()

    return report
