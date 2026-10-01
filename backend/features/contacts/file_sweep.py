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
пропускает пройденные окончательно (сайт, который не ответил, идёт снова —
`sweep_trace.py`), а CSV печатается из файла — поэтому он полный и после
обрыва, и после продолжения. Собери его из памяти процесса, и
продолженный прогон отдал бы только вторую половину.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from backend.config import contacts as cfg
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.messengers import MessengerKind, Trust
from backend.features.contacts.sweep_checkpoint import (
    MAX_ATTEMPTS,
    RETRY,
    UNREACHABLE,
    Checkpoint,
    done_hosts,
    retry_hosts,
    rows_for,
    rows_from_checkpoint,
)
from backend.features.contacts.sweep_input import DomainList, host_from_cell, read_hosts, read_list
from backend.features.contacts.sweep_trace import TracedRenderer, traced, watch
from backend.shared.net.url_guard import guarded_client

# Чтение списка, чекпойнт и след обмена с сайтом живут в своих модулях
# (`sweep_input.py`, `sweep_checkpoint.py`, `sweep_trace.py`), но входная
# точка прогона — здесь: команда и тесты берут всё из одного модуля.
__all__ = [
    "CONCURRENCY",
    "MAX_ATTEMPTS",
    "NETWORK_PROBES",
    "OUTPUT_COLUMNS",
    "RETRY",
    "UNREACHABLE",
    "DomainList",
    "NetworkDownError",
    "SweepReport",
    "done_hosts",
    "host_from_cell",
    "network_alive",
    "read_hosts",
    "read_list",
    "retry_hosts",
    "row_of",
    "rows_for",
    "rows_from_checkpoint",
    "sweep",
    "write_csv",
]

logger = logging.getLogger(__name__)

#: Сколько доменов проходим одновременно. То же число, что у поиска по базе:
#: больше — упрёмся в вежливость к чужим сайтам, меньше — прогон растянется.
CONCURRENCY = 8

#: Как часто сообщать о ходе дела. Прогон на часы без вывода читается
#: как зависший.
PROGRESS_EVERY = 25

#: Колонки итога. Каналы — по одному столбцу на вид, значения через `; `.
#: `retry_reason` заполнен только у строк «повторить»: почему исход не окончательный.
#: `mail_route` — вердикт MX по домену сайта: `mx` и `implicit` — почту
#: принимает, `none` — нет (адреса на нём отсеяны, см. `rejected`),
#: `unknown` — DNS не ответил, доставка не проверена.
OUTPUT_COLUMNS = (
    "host", "status", "retry_reason",
    "mail_route", "email", "emails", "email_source", "email_page", "has_form",
    "telegram", "skype", "whatsapp", "viber", "vk", "phone",
    "guessed", "handles_page", "rejected",
)  # fmt: skip


#: Заведомо живые адреса. По ним прогон проверяет сеть — до первого домена
#: и тогда, когда домен не ответил ничем. Любой HTTP-ответ — сеть есть.
NETWORK_PROBES = (
    "https://www.google.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://github.com/",
)


class NetworkDownError(RuntimeError):
    """Сеть недоступна: ни один контрольный адрес не ответил.

    Прогон останавливается, а не пишет домены «повторить»: попытка без сети
    — не попытка, и предел (`MAX_ATTEMPTS`) за три запуска без сети похоронил
    бы весь список (ревью #128). `report` — что успели до обрыва, если успели.
    """

    def __init__(self, message: str, *, report: SweepReport | None = None) -> None:
        super().__init__(message)
        self.report = report


async def network_alive(http: httpx.AsyncClient) -> bool:
    """Отвечает ли хоть один контрольный адрес. Ответ любой — сеть жива."""
    for url in NETWORK_PROBES:
        try:
            await http.get(url)
        except httpx.HTTPError as exc:
            logger.info("сеть: %s не ответил (%r)", url, exc)
            continue
        return True
    return False


@dataclass(slots=True)
class SweepReport:
    """Чем кончился прогон."""

    total: int = 0
    #: Пройдены окончательно.
    walked: int = 0
    #: Записаны «повторить»: сайт не ответил, закрылся или обход оборван.
    retry: int = 0
    #: Пройдены, но не проверены: «повторить» кончился пределом попыток.
    #: Входят и в `walked` — повторять их больше не будут.
    unreachable: int = 0
    #: Упали с ошибкой. Строки в чекпойнте нет — следующий запуск пройдёт их снова.
    failed: int = 0
    skipped: int = 0
    with_email: int = 0
    with_handle: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    #: Что пошло не так, как просили: «браузер не поднялся».
    notes: list[str] = field(default_factory=list)

    @property
    def with_any_contact(self) -> int:
        return self.with_email + self.with_handle

    @property
    def processed(self) -> int:
        return self.walked + self.retry + self.failed

    def count(self, row: dict[str, str], *, has_handles: bool) -> None:
        """Учесть строку, записанную в чекпойнт."""
        if row["status"] == RETRY:
            self.retry += 1
        else:
            self.walked += 1
            self.unreachable += row["status"] == UNREACHABLE
        self.with_email += bool(row["email"])
        self.with_handle += bool(not row["email"] and has_handles)


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


def _verdict_columns(result: LadderResult, retry: str | None) -> dict[str, str]:
    """Чем кончился проход: статус, почему «повторить» и вердикт MX.

    `retry` — почему исход не окончательный: тогда статус «повторить»,
    а найденное по дороге (каналы, форма) в строке остаётся.
    """
    return {
        "status": RETRY if retry else result.status.value,
        "retry_reason": retry or "",
        "mail_route": result.mail_route.value if result.mail_route else "",
    }


def row_of(result: LadderResult, *, retry: str | None = None) -> dict[str, str]:
    """Строка итога по одному домену. Пустые поля — законный исход."""
    emails = sorted({candidate.email for candidate in result.candidates})
    row = {
        "host": result.host,
        **_verdict_columns(result, retry),
        "email": result.contact.email if result.contact else "",
        "emails": "; ".join(emails),
        "email_source": result.source.value if result.source else "",
        "email_page": (result.contact.page_url or "") if result.contact else "",
        "has_form": "true" if result.has_form else "",
        "rejected": "; ".join(f"{email} ({reason})" for email, reason in result.rejected),
    }
    row.update(_handle_columns(result))
    return row


def _as_text(value: str) -> str:
    """Ячейка из одних цифр — формулой-строкой, чтобы Excel не счёл её числом.

    Иначе номер `0612345678` теряет ведущий ноль, а `79161234567890`
    становится 7,92E+13. `="…"` остаётся текстом в Excel и в Google
    Таблицах; файл и так собран под Excel, а читающему его программой
    обёртку снять проще, чем вернуть потерянные цифры.
    """
    return f'="{value}"' if value.isascii() and value.isdigit() else value


def write_csv(rows: Iterable[dict[str, str]], path: Path) -> int:
    """Собрать итоговый CSV. UTF-8 с BOM — чтобы Excel открыл его щелчком."""
    written = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS), delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _as_text(row.get(name, "")) for name in OUTPUT_COLUMNS})
            written += 1
    return written


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

    keeper = Checkpoint(checkpoint)
    limiter = asyncio.Semaphore(max(1, concurrency))
    timeout = httpx.Timeout(cfg.PAGE_TIMEOUT_SEC, connect=cfg.PAGE_TIMEOUT_SEC)

    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(guarded_client(timeout=timeout))
        # Сеть — до браузера: без неё Chromium поднимать незачем.
        if not await network_alive(http):
            raise NetworkDownError("ни один контрольный адрес не ответил — прогон не начат")
        # Браузер — только по просьбе: Chromium стоит секунд запуска и сотен
        # мегабайт, а без просьбы его ступень всё равно не работает.
        renderer = await stack.enter_async_context(PlaywrightRenderer()) if use_browser else None
        if use_browser and renderer is None:
            report.notes.append("Браузер не поднялся — прогон идёт без этой ступени.")
        # Обмен с каждым сайтом учитывается: по нему решается, пройден ли
        # домен окончательно или его надо повторить (`sweep_trace.py`).
        watch(http)
        lost = asyncio.Event()
        ladder = ContactLadder(
            http,
            provider=None,  # платных ступеней здесь нет вовсе
            # Ручной очереди у прогона по файлу нет: форма — колонка has_form,
            # а не заявка. Потолок на весь список не исчерпается, и лог не
            # скажет «ждёт следующего месяца» про домены, которые ничего не ждут.
            manual_queue_left=len(hosts),
            renderer=TracedRenderer(renderer) if renderer is not None else None,
            stop_without_mail=False,
            collect_handles=True,  # каналы связи — ради них прогон по файлу и нужен
        )

        async def one(host: str) -> None:
            async with limiter:
                if lost.is_set():
                    return
                await _walk_one(ladder, keeper, report, host, http=http, lost=lost)
                if on_progress is not None and report.processed % PROGRESS_EVERY == 0:
                    on_progress(report.processed, report.total)

        await asyncio.gather(*(one(host) for host in hosts))
        report.counters = ladder.counters.as_report()

    if lost.is_set():
        raise NetworkDownError(
            "сеть пропала посреди прогона — недоделанные домены ждут повтора", report=report
        )
    return report


async def _walk_one(
    ladder: ContactLadder,
    keeper: Checkpoint,
    report: SweepReport,
    host: str,
    *,
    http: httpx.AsyncClient,
    lost: asyncio.Event,
) -> None:
    """Один домен: лестница, вердикт «пройден или повторить», строка в чекпойнт."""
    with traced(host) as trace:
        try:
            result = await ladder.find(host)
        except Exception:
            # Падение на одном домене не должно стоить прогона, но и молчать
            # о нём нельзя: без строки в чекпойнте домен вернётся в следующий
            # запуск, и это правильный исход.
            logger.exception("прогон: домен %s не обошёлся", host)
            report.failed += 1
            return
    # Вердикт — после выхода из следа: запрос, так и не получивший ответа,
    # засчитывается отказом только при закрытии.
    row = row_of(result, retry=trace.retry_reason(result))
    if row["status"] == RETRY and not trace.heard and not await network_alive(http):
        # Сайт не ответил ничем, и контрольные адреса молчат — легла сеть, а не
        # сайт. Строку не пишем: попытка не сгорает, домен пойдёт заново.
        logger.warning("прогон: сеть пропала на домене %s — останавливаемся", host)
        lost.set()
        return
    row = await keeper.add(host, row)
    report.count(row, has_handles=bool(result.handles))
