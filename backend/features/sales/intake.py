"""Приём базы лидов: файл → предпросмотр с отчётом по строкам.

**Читает общий `contacts.sweep_input.read_records`** — второго читателя CSV нет:
кодировка (cp1251 из Excel — отказ словами), BOM, разделитель и номера строк файла
проверяются в одном месте для всех направлений. Здесь — логика продаж: заголовок,
сопоставление колонок и нормализация.

**Плохая строка стоит строки, а не загрузки**: в отчёте номер строки файла, причина
словами и ячейка как есть. Поле, которое разрешено не знать (сайт при рабочей почте,
страна, язык), непонятым строки не стоит — лид загружается с замечанием (урок L67
соседнего проекта).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS
from backend.features.contacts.sweep_input import host_from_cell, read_records
from backend.features.donors.host import normalize_host
from backend.features.sales.columns import LeadField, Mapping, guess

NO_ADDRESS = "нет адреса"
EMAIL_LENGTH = 254  # длиннее адресов не бывает, RFC 5321
HOST_LENGTH = 253  # и имён сайтов, RFC 1035
TEXT_LENGTH = 255  # ширина колонок имени, должности и компании
SAMPLE_ROWS = 5  # строк данных, по которым сопоставляют колонки руками

#: Коды страны и языка: не код — поле пусто, замечание в отчёте.
_CODES = {
    LeadField.COUNTRY: (re.compile(r"[a-z]{2}"), "страна не записана: ждём код — de, us"),
    LeadField.LANGUAGE: (
        re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?"),
        "язык не записан: ждём код — en, ru",
    ),
}


class IntakeError(ValueError):
    """Источник не читается целиком или сопоставление не годится. Текст — что делать."""


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
    country: str | None = None
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


def preview(table: Table, mapping: Mapping | None = None, *, header: bool | None = None) -> Preview:
    """Сухой прогон: что станет лидами и что нет.

    Заголовок — первая строка, если в ней есть знакомое имя колонки; `header`
    решает вместо угадывания. Сопоставление руками заменяет угаданное целиком.
    """
    first = table.records[0][1]
    guessed = guess(first)
    titled = bool(guessed) if header is None else header
    records = table.records[1:] if titled else table.records
    if not records:
        raise IntakeError(f"{table.source}: только заголовок — строк с лидами нет")
    width = max(len(cells) for _, cells in table.records)
    chosen = (guessed if titled else {}) if mapping is None else _within(mapping, width)
    leads, problems = _rows(records, chosen) if LeadField.EMAIL in chosen else ([], [])
    return Preview(
        source=table.source,
        header=titled,
        columns=[_title(first, i) if titled else f"колонка {i + 1}" for i in range(width)],
        sample=[cells for _, cells in records[:SAMPLE_ROWS]],
        mapping=chosen,
        rows=len(records),
        leads=leads,
        problems=problems,
    )


def _title(first: list[str], index: int) -> str:
    title = first[index].strip() if index < len(first) else ""
    return title or f"колонка {index + 1}"


def _within(mapping: Mapping, width: int) -> Mapping:
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
    if len(email) > EMAIL_LENGTH or not EMAIL_RE.fullmatch(email) or not _host(mailbox):
        return None, [Problem(line, "не адрес почты" if email else NO_ADDRESS, raw)]
    domain, note = _company(line, mailbox, raw, row.get(LeadField.WEBSITE, ""))
    notes = [note] if note else []
    if domain is None:
        return None, notes

    def value(field: LeadField, text: str | None = None) -> str | None:
        return _value(line, field, row.get(field, "") if text is None else text, notes)

    lead = Lead(
        line,
        email,
        domain,
        name=value(LeadField.NAME, _person(row)),
        position=value(LeadField.POSITION),
        company=value(LeadField.COMPANY),
        country=value(LeadField.COUNTRY),
        language=value(LeadField.LANGUAGE),
    )
    return lead, notes


def _company(line: int, mailbox: str, raw: str, site: str) -> tuple[str | None, Problem | None]:
    """Домен компании — из сайта, иначе из рабочей почты. Бесплатная почта его не даёт."""
    host = _host(site)
    if host:
        return host, None
    root = _host(mailbox)
    if not {mailbox, root} & FREE_MAILBOX_DOMAINS:
        unread = f"сайт не разобран — домен компании {root} взят из почты"
        return root, Problem(line, unread, site, loaded=True) if site else None
    if site:
        reason = f"нет сайта компании: «{site}» — не адрес сайта, а {mailbox} — бесплатная почта"
        return None, Problem(line, reason, site)
    return None, Problem(line, f"нет сайта компании: {mailbox} — бесплатная почта", raw)


def _host(value: str) -> str:
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
    """Код страны и языка — нижним регистром, текст — по ширине колонки. Непонятое
    строки не стоит: поле пусто или обрезано, замечание — в отчёте."""
    if field in _CODES:
        pattern, reason = _CODES[field]
        if not text or pattern.fullmatch(text.lower()):
            return text.lower() or None
        notes.append(Problem(line, reason, text, loaded=True))
        return None
    if len(text) > TEXT_LENGTH:
        notes.append(Problem(line, f"длиннее {TEXT_LENGTH} знаков — обрезано", text, loaded=True))
    return text[:TEXT_LENGTH] or None
