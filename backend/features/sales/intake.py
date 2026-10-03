"""Приём базы лидов: файл или Google-таблица → предпросмотр с отчётом → лиды.

**Читает общий `contacts.sweep_input.read_records`** — второго читателя CSV нет:
кодировка (cp1251 из Excel — отказ словами), BOM, разделитель и номера строк файла
проверяются в одном месте для всех направлений. Здесь — логика продаж: заголовок,
сопоставление колонок и нормализация.

**Плохая строка стоит строки, а не загрузки**: в отчёте номер строки файла, причина
словами и ячейка как есть. Поле, которое разрешено не знать (сайт при рабочей почте,
страна, пояс, язык), непонятым строки не стоит — лид загружается с замечанием (урок L67
соседнего проекта).

**Страна — кодом по таблице названий (`geo.py`), пояс — из колонки файла, иначе по
стране.** Пояс нужен отправке: письмо уходит в рабочие часы получателя. У страны
с несколькими поясами пояс по стране — столичный, и это замечание: точнее скажет
колонка файла.

**Адрес лида — только в `sales_leads.email`** (решение (а) от 01.10): строк `contacts`
приём не заводит. **Предпросмотр базы не касается** — пишет только `load`.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS
from backend.features.contacts.sweep_input import host_from_cell, read_records
from backend.features.core.domain import AuditAction
from backend.features.donors.host import normalize_host
from backend.features.donors.repository import DonorRepository
from backend.features.sales import geo, sheet
from backend.features.sales.columns import LeadField, Mapping, guess
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)

logger = logging.getLogger(__name__)

#: Предел файла и таблицы по ссылке; 5000 лидов — около половины мегабайта.
MAX_BYTES = 10 * 1024 * 1024
SHEET_NAME = "Google-таблица"
NO_ADDRESS = "нет адреса"
EMAIL_LENGTH = 254  # длиннее адресов не бывает, RFC 5321
HOST_LENGTH = 253  # и имён сайтов, RFC 1035
TEXT_LENGTH = 255  # ширина колонок имени, должности и компании
SAMPLE_ROWS = 5  # строк данных, по которым сопоставляют колонки руками
#: Доменов на запрос: хосты уходят параметрами, а их у Postgres не больше 32 767.
CHUNK = 1000

#: Код языка: не код — поле пусто, замечание в отчёте.
_LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?")
NO_LANGUAGE = "язык не записан: ждём код — en, ru"
NO_COUNTRY = "страна не распознана: ждём код или название — de, Germany, Германия"
NO_ZONE = "часовой пояс не распознан: ждём имя из базы поясов — Europe/Berlin"
_UNSAFE_NAME = re.compile(r"[^\w.\- ]")


class IntakeError(ValueError):
    """Источник не читается целиком или сопоставление не годится. Текст — что делать."""


class UnknownHypothesisError(LookupError):
    """Гипотезы, в которую грузят, нет."""


@dataclass(frozen=True, slots=True)
class Table:
    """Непустые записи источника с номерами строк файла."""

    source: str
    records: list[tuple[int, list[str]]]


@dataclass(frozen=True, slots=True)
class Problem:
    """Строка отчёта: номер строки файла, причина словами, ячейка как есть."""

    line: int
    reason: str
    cell: str
    #: Лид загружен, пусто только это поле. `False` — строка отклонена.
    loaded: bool = False


@dataclass(frozen=True, slots=True)
class Lead:
    """Лид, готовый к записи: адрес и домен компании нормализованы."""

    line: int
    email: str
    domain: str
    name: str | None = None
    position: str | None = None
    company: str | None = None
    #: ISO-2 нижним регистром; пояс — имя из базы поясов (`Europe/Berlin`).
    country: str | None = None
    timezone: str | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class Preview:
    """Что выйдет из загрузки. Строится без базы."""

    source: str
    #: Первая строка — заголовок: угадано по именам колонок или сказано человеком.
    header: bool
    columns: list[str]
    sample: list[list[str]]
    mapping: Mapping
    rows: int
    leads: list[Lead]
    problems: list[Problem]

    @property
    def needs_mapping(self) -> bool:
        """Колонки почты нет — лидов не считали: правда «сопоставьте», а не «0 лидов»."""
        return LeadField.EMAIL not in self.mapping

    @property
    def rejected(self) -> int:
        return sum(1 for problem in self.problems if not problem.loaded)


def read_file(path: Path, *, delimiter: str | None = None) -> Table:
    """Таблица с диска. Отказ целиком — `IntakeError` со словами общего читателя."""
    try:
        records = read_records(path, delimiter=delimiter)
    except OSError as exc:
        raise IntakeError(f"файл {path} не открылся: {exc.strerror or exc}") from exc
    except csv.Error as exc:
        raise IntakeError(f"файл {path.name} не читается как CSV: {exc}") from exc
    except ValueError as exc:  # не UTF-8, невозможный разделитель — уже словами
        raise IntakeError(str(exc)) from exc
    if not records:
        raise IntakeError(f"в файле {path.name} нет ни одной строки")
    return Table(path.name, records)


def read_bytes(data: bytes, name: str, *, delimiter: str | None = None) -> Table:
    """Присланное с экрана или скачанное по ссылке — через временный файл: общий
    читатель берёт путь. Имя файла сохраняется — его называют отказы."""
    if len(data) > MAX_BYTES:
        raise IntakeError(f"файл {name} больше {MAX_BYTES / 2**20:g} МБ — разделите базу")
    clean = _UNSAFE_NAME.sub("_", Path(name).name).lstrip(".")[:100] or "база.csv"
    with tempfile.TemporaryDirectory(prefix="sales-intake-") as folder:
        path = Path(folder) / clean
        path.write_bytes(data)
        return read_file(path, delimiter=delimiter)


async def read_link(link: str, http: httpx.AsyncClient, *, delimiter: str | None = None) -> Table:
    """Лист Google-таблицы — тем же путём, что файл."""
    data = await sheet.fetch(link, http, limit=MAX_BYTES)
    table = await asyncio.to_thread(read_bytes, data, f"{SHEET_NAME}.csv", delimiter=delimiter)
    return dataclasses.replace(table, source=SHEET_NAME)


def preview(table: Table, mapping: Mapping | None = None, *, header: bool | None = None) -> Preview:
    """Сухой прогон: что станет лидами и что нет.

    Заголовок — первая строка, если в ней есть знакомое имя колонки; `header`
    решает вместо угадывания. Сопоставление руками заменяет угаданное целиком.
    """
    first = table.records[0][1]
    titled = bool(guess(first)) if header is None else header
    titles = first if titled else []
    records = table.records[1:] if titled else table.records
    if not records:
        raise IntakeError(f"{table.source}: только заголовок — строк с лидами нет")
    width = max(len(cells) for _, cells in table.records)
    chosen = _chosen(mapping, guess(titles), width)
    leads, problems = _rows(records, chosen) if LeadField.EMAIL in chosen else ([], [])
    return Preview(
        source=table.source,
        header=titled,
        columns=_columns(titles, width),
        sample=[cells for _, cells in records[:SAMPLE_ROWS]],
        mapping=chosen,
        rows=len(records),
        leads=leads,
        problems=problems,
    )


def _columns(titles: list[str], width: int) -> list[str]:
    """Имена колонок; нет заголовка или ячейка пуста — «колонка N»."""
    named = [title.strip() for title in titles] + [""] * (width - len(titles))
    return [title or f"колонка {i}" for i, title in enumerate(named, start=1)]


def _chosen(mapping: Mapping | None, guessed: Mapping, width: int) -> Mapping:
    """Сопоставление руками заменяет угаданное целиком — если колонки есть в файле."""
    if mapping is None:
        return guessed
    for field, index in mapping.items():
        if not 0 <= index < width:
            raise IntakeError(
                f"колонки {index + 1} нет, их в файле {width}: {field} не сопоставить"
            )
    return dict(mapping)


def _rows(
    records: list[tuple[int, list[str]]], mapping: Mapping
) -> tuple[list[Lead], list[Problem]]:
    leads: list[Lead] = []
    problems: list[Problem] = []
    for line, cells in records:
        row = {field: cells[i].strip() for field, i in mapping.items() if i < len(cells)}
        lead, notes = _lead(line, row)
        problems += notes
        if lead is not None:
            leads.append(lead)
    return leads, problems


def _lead(line: int, row: dict[LeadField, str]) -> tuple[Lead | None, list[Problem]]:
    """Строка файла → лид с замечаниями или отказ строки."""
    raw = row.get(LeadField.EMAIL, "")
    email = "".join(raw.split()).lower()
    mailbox = email.rpartition("@")[2]
    if len(email) > EMAIL_LENGTH or not EMAIL_RE.fullmatch(email) or not host_key(mailbox):
        return None, [Problem(line, "не адрес почты" if email else NO_ADDRESS, raw)]
    domain, note = _company(line, mailbox, raw, row.get(LeadField.WEBSITE, ""))
    notes = [note] if note else []
    if domain is None:
        return None, notes

    def value(field: LeadField, text: str | None = None) -> str | None:
        return _value(line, field, row.get(field, "") if text is None else text, notes)

    where = row.get(LeadField.COUNTRY, "")
    country = _country(line, where, notes)
    lead = Lead(
        line,
        email,
        domain,
        name=value(LeadField.NAME, _person(row)),
        position=value(LeadField.POSITION),
        company=value(LeadField.COMPANY),
        country=country,
        timezone=_timezone(line, row.get(LeadField.TIMEZONE, ""), (where, country), notes),
        language=value(LeadField.LANGUAGE),
    )
    return lead, notes


def _country(line: int, text: str, notes: list[Problem]) -> str | None:
    """`DE`, `Germany`, `Германия` → `de` по таблице `geo`. Непонятое — пусто и замечание."""
    if not text:
        return None
    code = geo.country_code(text)
    if code is None:
        notes.append(Problem(line, NO_COUNTRY, text, loaded=True))
    return code


def _timezone(
    line: int, text: str, country: tuple[str, str | None], notes: list[Problem]
) -> str | None:
    """Пояс из колонки файла, иначе по стране (ячейка как в файле и её код).

    Колонка побеждает: она точнее страны (у США четыре пояса), а непонятая —
    замечание и пояс по стране. У страны с несколькими поясами пояс столичный
    (решение владельца 04.10) — с замечанием: окно отправки есть, а точнее
    скажет только колонка.
    """
    if text:
        if zone := geo.zone_name(text):
            return zone
        notes.append(Problem(line, NO_ZONE, text, loaded=True))
    cell, code = country
    if code is None:
        return None
    zone = geo.timezone_for(code)
    if zone is None:
        none = f"часовой пояс не определён: у страны {code} нет единого пояса — нужна колонка пояса"
        notes.append(Problem(line, none, cell, loaded=True))
    elif geo.by_capital(code):
        capital = (
            f"часовой пояс по столице — {zone}: у страны {code} их несколько, в файле не задан"
        )
        notes.append(Problem(line, capital, cell, loaded=True))
    return zone


def _company(line: int, mailbox: str, raw: str, site: str) -> tuple[str | None, Problem | None]:
    """Домен компании — из сайта, иначе из рабочей почты. Бесплатная почта его не даёт."""
    host = host_key(site)
    if host:
        return host, None
    root = host_key(mailbox)
    if not {mailbox, root} & FREE_MAILBOX_DOMAINS:
        unread = f"сайт не разобран — домен компании {root} взят из почты"
        return root, Problem(line, unread, site, loaded=True) if site else None
    if site:
        reason = f"нет сайта компании: «{site}» — не адрес сайта, а {mailbox} — бесплатная почта"
        return None, Problem(line, reason, site)
    return None, Problem(line, f"нет сайта компании: {mailbox} — бесплатная почта", raw)


def host_key(value: str) -> str:
    """Хост → корневой домен, как у доноров: `domains.host` — ключ дедупликации.

    Сначала `host_from_cell`: он не бросает на мусоре вроде `site.com[old]`.
    Зона не из списка публичных суффиксов (`.test`, свежая зона) — хост целиком:
    без списка границу компании не провести, а терять лида незачем.
    """
    host = host_from_cell(value)
    # Длиннее колонки `domains.host` имён не бывает, а мусор такой длины уронил бы запись.
    return (normalize_host(host) or host) if 0 < len(host) <= HOST_LENGTH else ""


def _person(row: dict[LeadField, str]) -> str:
    """Имя целиком: «Имя» и «Фамилия» складываются, полное имя не повторяется."""
    given = row.get(LeadField.NAME) or row.get(LeadField.FIRST_NAME, "")
    last = row.get(LeadField.LAST_NAME, "")
    return f"{given} {last}".strip() if last not in given else given


def _value(line: int, field: LeadField, text: str, notes: list[Problem]) -> str | None:
    """Код языка — нижним регистром, текст — по ширине колонки. Непонятое
    строки не стоит: поле пусто или обрезано, замечание — в отчёте."""
    if field is LeadField.LANGUAGE:
        if not text or _LANGUAGE.fullmatch(text.lower()):
            return text.lower() or None
        notes.append(Problem(line, NO_LANGUAGE, text, loaded=True))
        return None
    if len(text) > TEXT_LENGTH:
        notes.append(Problem(line, f"длиннее {TEXT_LENGTH} знаков — обрезано", text, loaded=True))
    return text[:TEXT_LENGTH] or None


async def load(
    session: AsyncSession, found: Preview, hypothesis_id: int, *, author_id: int | None
) -> int:
    """Записать лидов предпросмотра в гипотезу. Коммит — за вызывающим.

    Журнал пишется здесь, а не в обвязке: загрузку зовут и экран, и консоль,
    и запись не должна зависеть от того, кто позвал (урок L63 соседнего проекта).
    """
    if found.needs_mapping:
        raise IntakeError("сопоставьте колонки: не найдена колонка почты — без неё лидов нет")
    if await session.get(SalesHypothesisModel, hypothesis_id) is None:
        raise UnknownHypothesisError(
            f"гипотезы №{hypothesis_id} нет — заведите её: outreach sales-hypothesis-add"
        )
    if not found.leads:
        return 0
    hosts = list(dict.fromkeys(lead.domain for lead in found.leads))
    ids: dict[str, int] = {}
    domains = DonorRepository(session)
    for start in range(0, len(hosts), CHUNK):
        ids |= await domains.ensure_domains(hosts[start : start + CHUNK])
    rows = [_values(lead, ids[lead.domain], hypothesis_id) for lead in found.leads]
    await session.execute(insert(SalesLeadModel), rows)
    loaded, rejected = len(rows), found.rejected
    await AccessRepository(session).record(
        AuditAction.SALES_LEADS_IMPORTED,
        author_id=author_id,
        target=f"sales_hypothesis:{hypothesis_id}",
        details={"источник": found.source, "загружено": loaded, "отклонено": rejected},
    )
    logger.info("продажи: база загружена", extra={"hypothesis_id": hypothesis_id, "loaded": loaded})
    return loaded


def _values(lead: Lead, domain_id: int, hypothesis_id: int) -> dict[str, Any]:
    """Строка `sales_leads`: `contact_id` пуст — адрес лида живёт только у лида."""
    values = dataclasses.asdict(lead)
    del values["line"], values["domain"]
    status = {"source": LeadSource.IMPORT, "status": LeadStatus.NEW}
    return {**values, **status, "hypothesis_id": hypothesis_id, "domain_id": domain_id}
