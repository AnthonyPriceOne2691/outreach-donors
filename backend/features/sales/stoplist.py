"""Ручной стоп-лист продаж: домены и адреса клиентов и партнёров, которым не пишем.

Список приходит файлом — колонка доменов и адресов вперемешку, как его
ведут люди. Читает его общий `contacts.sweep_input.read_records` (кодировка,
BOM, разделитель — один судья на все направления), а что в ячейке — домен
или адрес — решается здесь: адрес узнаётся по «@» и общей форме `EMAIL_RE`,
домен сводится к корню тем же путём, что домен компании лида.

Строка, которую не разобрали, называется с номером и ячейкой, а не теряется;
одно исключение — первая строка файла: не домен и не адрес в ней — заголовок,
как у общего `read_list` («строка с доменом — данные»). Повтор — не ошибка:
список присылают целиком и не раз, и «уже есть» считается отдельно от
«добавлено».

Почему своя таблица, а не общие `suppressions`, — в `models.SalesStoplistModel`.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.sweep_input import read_records
from backend.features.core.domain import AuditAction
from backend.features.sales.intake import EMAIL_LENGTH, host_key
from backend.features.sales.models import SalesStoplistModel

logger = logging.getLogger(__name__)

NOT_AN_ENTRY = "не домен и не адрес"


class StoplistError(ValueError):
    """Файл не читается целиком. Текст — что делать."""


@dataclass(frozen=True, slots=True)
class Entries:
    """Что прочитано из файла: домены, адреса и строки, которые не разобрались."""

    hosts: list[str]
    emails: list[str]
    unreadable: list[tuple[int, str]]


@dataclass(frozen=True, slots=True)
class Loaded:
    """Итог записи: сколько строк легло, сколько уже было, что не разобралось."""

    added: int
    known: int
    unreadable: list[tuple[int, str]]


def read_entries(path: Path, *, delimiter: str | None = None) -> Entries:
    """Первая ячейка каждой строки — домен или адрес. Отказ целиком — словами."""
    try:
        records = read_records(path, delimiter=delimiter)
    except OSError as exc:
        raise StoplistError(f"файл {path} не открылся: {exc.strerror or exc}") from exc
    except csv.Error as exc:
        raise StoplistError(f"файл {path.name} не читается как CSV: {exc}") from exc
    except ValueError as exc:  # не UTF-8 — уже словами
        raise StoplistError(str(exc)) from exc
    hosts: dict[str, None] = {}
    emails: dict[str, None] = {}
    unreadable: list[tuple[int, str]] = []
    for number, (line, cells) in enumerate(records):
        cell = next((cell.strip() for cell in cells if cell.strip()), "")
        kind, value = _entry(cell)
        if kind == "email":
            emails[value] = None
        elif kind == "host":
            hosts[value] = None
        elif number:  # первая нечитаемая строка файла — заголовок, не ошибка
            unreadable.append((line, cell))
    return Entries(list(hosts), list(emails), unreadable)


def _entry(cell: str) -> tuple[str, str]:
    """Что в ячейке: («email», адрес), («host», корень домена) или («», ячейка)."""
    email = "".join(cell.split()).lower()
    if "@" in email and len(email) <= EMAIL_LENGTH and EMAIL_RE.fullmatch(email):
        return "email", email
    if host := host_key(cell):
        return "host", host
    return "", cell


async def add(session: AsyncSession, entries: Entries, *, author: str, note: str | None) -> Loaded:
    """Записать строки; повторы пропускаются молча и считаются. Коммит — за вызывающим.

    Журнал — одной записью на загрузку: она меняет, кому продажи напишут,
    и по журналу потом ищут, откуда взялся запрет.
    """
    added = 0
    for column, values in (("host", entries.hosts), ("email", entries.emails)):
        if values:
            added += await _insert(session, column, values, author=author, note=note)
    if added:
        await AccessRepository(session).record(
            AuditAction.SUPPRESSION_ADDED,
            target="sales_stoplist",
            details={
                "источник": note or author,
                "доменов": len(entries.hosts),
                "адресов": len(entries.emails),
                "добавлено": added,
            },
        )
    logger.info("продажи: стоп-лист пополнен", extra={"added": added, "author": author})
    return Loaded(added, len(entries.hosts) + len(entries.emails) - added, entries.unreadable)


async def _insert(
    session: AsyncSession, column: str, values: list[str], *, author: str, note: str | None
) -> int:
    rows: list[dict[str, Any]] = [
        {column: value, "created_by": author, "note": note} for value in values
    ]
    statement = (
        insert(SalesStoplistModel)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[column])
        .returning(SalesStoplistModel.id)
    )
    return len((await session.execute(statement)).all())
