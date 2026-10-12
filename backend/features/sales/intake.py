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
колонка файла. **Язык — основой кода** (`ru-RU` → `ru`, `lead_values.py`).

**Язык и страна или пояс по умолчанию** — на всю загрузку, только в строки, где колонка
пуста (`lead_values.Defaults`); сколько строк их получили — в отчёте. Без чего письма не
уйдут (нет языка, языка без цепочки, ни страны, ни пояса) — словами до загрузки
(`Preview.warnings`), а не молча.

**Адрес лида — только в `sales_leads.email`** (решение (а) от 01.10): строк `contacts`
приём не заводит. **Предпросмотр базы не касается** — пишет только `load`.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import logging
import re
from collections import Counter
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
from backend.features.sales import geo, lead_values, sheet, upload
from backend.features.sales.columns import LeadField, Mapping, guess
from backend.features.sales.lead_values import NO_DEFAULTS, Defaults
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)

logger = logging.getLogger(__name__)

#: Предел файла и таблицы по ссылке; 5000 лидов — около половины мегабайта. Файл с экрана идёт
#: телом формы через оба nginx, а у них 8 МБ на всё тело с обёрткой формы: мегабайт запаса — ей,
#: иначе прокси отбил бы файл страницей 413 мимо слов отказа (`tests/test_sales_intake_limit.py`).
MAX_BYTES = 7 * 1024 * 1024
SHEET_NAME = "Google-таблица"
NO_ADDRESS = "нет адреса"
EMAIL_LENGTH = 254  # длиннее адресов не бывает, RFC 5321
HOST_LENGTH = 253  # и имён сайтов, RFC 1035
TEXT_LENGTH = 255  # ширина колонок имени, должности и компании
SAMPLE_ROWS = 5  # строк данных, по которым сопоставляют колонки руками
#: Доменов на запрос: хосты уходят параметрами, а их у Postgres не больше 32 767.
CHUNK = 1000

#: Код языка у записи базы знаний (`kb.py`): агент сводит язык лида с языком фактов. Язык
#: лида — основой кода (`lead_values.language_code`): не код — поле пусто, замечание в отчёте.
LANGUAGE_CODE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?")
NO_LANGUAGE = f"язык не записан: {lead_values.LANGUAGE_HINT}"
NO_COUNTRY = "страна не распознана: ждём код или название — de, Germany, Германия"
NO_ZONE = "часовой пояс не распознан: ждём имя из базы поясов — Europe/Berlin"


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
    #: Умолчания загрузки и сколько лидов их получили: колонка была пуста.
    defaults: Defaults = NO_DEFAULTS
    defaulted_language: int = 0
    defaulted_place: int = 0

    @property
    def warnings(self) -> list[str]:
        """Без чего письма не уйдут — словами, с числом лидов (`lead_values.warnings`)."""
        found = lead_values.warnings(self.leads)
        return [note, *found] if (note := self.defaults.note) and self.defaulted_place else found

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
    читатель берёт путь. Имя файла — правилом `upload.file_name`: его называют отказы."""
    if len(data) > MAX_BYTES:
        raise IntakeError(f"файл {name} больше {MAX_BYTES / 2**20:g} МБ — разделите базу")
    with upload.saved(data, name, fallback="база.csv") as path:
        return read_file(path, delimiter=delimiter)


async def read_link(link: str, http: httpx.AsyncClient, *, delimiter: str | None = None) -> Table:
    """Лист Google-таблицы — тем же путём, что файл."""
    data = await sheet.fetch(link, http, limit=MAX_BYTES)
    table = await asyncio.to_thread(read_bytes, data, f"{SHEET_NAME}.csv", delimiter=delimiter)
    return dataclasses.replace(table, source=SHEET_NAME)


def defaults(language: str | None = None, where: str | None = None) -> Defaults:
    """Язык и страна или пояс по умолчанию словами человека. Не годится — `IntakeError`."""
    try:
        return lead_values.defaults(language, where)
    except lead_values.ValueRefusedError as exc:
        raise IntakeError(str(exc)) from exc


def preview(
    table: Table,
    mapping: Mapping | None = None,
    *,
    header: bool | None = None,
    given: Defaults = NO_DEFAULTS,
) -> Preview:
    """Сухой прогон: что станет лидами и что нет.

    Заголовок — первая строка, если в ней есть знакомое имя колонки; `header`
    решает вместо угадывания. Сопоставление руками заменяет угаданное целиком.
    Умолчания `given` ложатся только туда, где колонка пуста.
    """
    first = table.records[0][1]
    titled = bool(guess(first)) if header is None else header
    titles = first if titled else []
    records = table.records[1:] if titled else table.records
    if not records:
        raise IntakeError(f"{table.source}: только заголовок — строк с лидами нет")
    width = max(len(cells) for _, cells in table.records)
    chosen = _chosen(mapping, guess(titles), width)
    rows = _rows(records, chosen, given) if LeadField.EMAIL in chosen else _Rows()
    return Preview(
        source=table.source,
        header=titled,
        columns=_columns(titles, width),
        sample=[cells for _, cells in records[:SAMPLE_ROWS]],
        mapping=chosen,
        rows=len(records),
        leads=rows.leads,
        problems=rows.problems,
        defaults=given,
        defaulted_language=rows.defaulted["language"],
        defaulted_place=rows.defaulted["place"],
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


@dataclass(slots=True)
class _Rows:
    """Строки файла, прочитанные целиком: лиды, отчёт и сколько получили умолчания."""

    leads: list[Lead] = dataclasses.field(default_factory=list)
    problems: list[Problem] = dataclasses.field(default_factory=list)
    defaulted: Counter[str] = dataclasses.field(default_factory=Counter)


def _rows(records: list[tuple[int, list[str]]], mapping: Mapping, given: Defaults) -> _Rows:
    found = _Rows()
    for line, cells in records:
        row = {field: cells[i].strip() for field, i in mapping.items() if i < len(cells)}
        lead, notes = _lead(line, row)
        found.problems += notes
        if lead is not None:
            found.leads.append(_filled(lead, row, given, found.defaulted))
    return found


def _filled(lead: Lead, row: dict[LeadField, str], given: Defaults, used: Counter[str]) -> Lead:
    """Умолчания — только где колонка пуста: язык — где пуста колонка языка, страна или
    пояс — где пусты обе колонки места. Колонка файла точнее умолчания, а непонятая ячейка —
    не пустая: её лид ждёт с замечанием."""
    changes: dict[str, Any] = {}
    if given.language and not row.get(LeadField.LANGUAGE):
        changes["language"] = given.language
        used["language"] += 1
    if given.place and not (row.get(LeadField.COUNTRY) or row.get(LeadField.TIMEZONE)):
        changes |= {"country": given.place.country, "timezone": given.place.timezone}
        used["place"] += 1
    return dataclasses.replace(lead, **changes) if changes else lead


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
    """Код языка — основой, нижним регистром (`ru-RU` → `ru`), текст — по ширине колонки.
    Непонятое строки не стоит: поле пусто или обрезано, замечание — в отчёте."""
    if field is LeadField.LANGUAGE:
        if code := lead_values.language_code(text):
            return code
        if text:
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
            f"гипотезы №{hypothesis_id} нет — обновите список гипотез или заведите новую"
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
    details: dict[str, Any] = {"источник": found.source, "загружено": loaded, "отклонено": rejected}
    if given := _given(found):
        details["по умолчанию"] = given
    await AccessRepository(session).record(
        AuditAction.SALES_LEADS_IMPORTED,
        author_id=author_id,
        target=f"sales_hypothesis:{hypothesis_id}",
        details=details,
    )
    logger.info("продажи: база загружена", extra={"hypothesis_id": hypothesis_id, "loaded": loaded})
    return loaded


def _given(found: Preview) -> dict[str, Any]:
    """Умолчания, легшие в лидов, — в журнал загрузки: откуда у лида язык, страна и пояс."""
    given, used = found.defaults, {}
    if found.defaulted_language:
        used["язык"] = {"значение": given.language, "лидов": found.defaulted_language}
    if found.defaulted_place and given.place is not None:
        where = {"страна": given.place.country, "пояс": given.place.timezone}
        used["страна или пояс"] = {**where, "лидов": found.defaulted_place}
    return used


def _values(lead: Lead, domain_id: int, hypothesis_id: int) -> dict[str, Any]:
    """Строка `sales_leads`: `contact_id` пуст — адрес лида живёт только у лида."""
    values = dataclasses.asdict(lead)
    del values["line"], values["domain"]
    status = {"source": LeadSource.IMPORT, "status": LeadStatus.NEW}
    return {**values, **status, "hypothesis_id": hypothesis_id, "domain_id": domain_id}
