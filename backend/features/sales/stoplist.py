"""Ручной стоп-лист продаж: домены и адреса клиентов и партнёров, которым не пишем.

Список приходит файлом — колонка доменов и адресов вперемешку, как его
ведут люди. Читает его общий `contacts.sweep_input.read_records` (кодировка,
BOM, разделитель — один судья на все направления), а что в ячейке — домен
или адрес — решается здесь: адрес узнаётся по «@» и общей форме `EMAIL_RE`,
домен сводится к корню тем же путём, что домен компании лида.

Строка, которую не разобрали, называется с номером и ячейкой, а не теряется;
одно исключение — первая строка файла: не домен и не адрес в ней — заголовок,
как у общего `read_list` («строка с доменом — данные»). Список, вставленный на экране
(`read_text`), заголовка не знает: замечание получает каждая непонятая строка. Домен
бесплатной почты (`gmail.com`) не записывается: он закрыл бы всех лидов с адресом на нём —
клиента на такой почте пишут адресом целиком. Повтор — не ошибка: список присылают
целиком и не раз, и «уже есть» считается отдельно от «добавлено».

**Пополнение снимает лидов, снятие записи — возвращает** (`stoplist_leads.py`, правилом
очистки): с экрана и из консоли — одним путём, `add` и `remove`; журнал — здесь же.

Почему своя таблица, а не общие `suppressions`, — в `models.SalesStoplistModel`.
"""

from __future__ import annotations

import csv
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS
from backend.features.contacts.sweep_input import read_records
from backend.features.core.domain import AuditAction
from backend.features.sales import stoplist_leads
from backend.features.sales.intake import EMAIL_LENGTH, MAX_BYTES, host_key
from backend.features.sales.models import SalesStoplistModel

logger = logging.getLogger(__name__)

NOT_AN_ENTRY = "не домен и не адрес"
FREE_MAIL = "домен бесплатной почты закрыл бы всех лидов на нём — впишите адрес целиком"
#: Ширина колонки `note`: откуда строка или почему не пишем — словами человека.
NOTE_LENGTH = 255


class StoplistError(ValueError):
    """Файл не читается целиком. Текст — что делать."""


class UnknownEntryError(LookupError):
    """Записи стоп-листа с таким номером нет."""


def _remarks(
    unreadable: list[tuple[int, str]], free: list[tuple[int, str]]
) -> list[tuple[int, str, str]]:
    """Строки, которые не легли, — номер, почему словами, ячейка; по порядку строк."""
    said = [(line, NOT_AN_ENTRY, cell) for line, cell in unreadable]
    return sorted(said + [(line, FREE_MAIL, cell) for line, cell in free])


@dataclass(frozen=True, slots=True)
class Entries:
    """Что прочитано: домены, адреса и строки, которые не разобрались, — номер и ячейка."""

    hosts: list[str]
    emails: list[str]
    unreadable: list[tuple[int, str]]
    #: Домены бесплатной почты: не записываются (`FREE_MAIL`).
    free: list[tuple[int, str]] = field(default_factory=list)

    @property
    def remarks(self) -> list[tuple[int, str, str]]:
        return _remarks(self.unreadable, self.free)


@dataclass(frozen=True, slots=True)
class Loaded:
    """Итог записи: сколько строк легло, сколько уже было, что не разобралось — и сколько
    лидов сняло пополнение."""

    added: int
    known: int
    unreadable: list[tuple[int, str]]
    free: list[tuple[int, str]] = field(default_factory=list)
    closed: int = 0

    @property
    def remarks(self) -> list[tuple[int, str, str]]:
        return _remarks(self.unreadable, self.free)


@dataclass(frozen=True, slots=True)
class Removed:
    """Снятая запись — кому снова пишем — и сколько лидов вернулось на очистку."""

    target: str
    released: int


def read_text(text: str) -> Entries:
    """Список, вставленный на экране: строка — домен или адрес (из таблицы — первая ячейка).
    Заголовка нет: непонятая первая строка — тоже замечание."""
    return _read_bytes(text.encode("utf-8"), "список.csv", header=False)


def read_upload(data: bytes, name: str) -> Entries:
    """Файл с экрана — как файл консоли (`read_entries`): первая непонятая строка — заголовок."""
    return _read_bytes(data, Path(name).name or "стоп-лист.csv", header=True)


def _read_bytes(data: bytes, name: str, *, header: bool) -> Entries:
    """Через временный файл: общий читатель берёт путь. Имя — то, что назовут отказы."""
    if len(data) > MAX_BYTES:
        raise StoplistError(f"список больше {MAX_BYTES / 2**20:g} МБ — разделите его")
    with tempfile.TemporaryDirectory(prefix="sales-stoplist-") as folder:
        path = Path(folder) / "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        path.write_bytes(data)
        return read_entries(path, header=header)


def read_entries(path: Path, *, delimiter: str | None = None, header: bool = True) -> Entries:
    """Первая ячейка каждой строки — домен или адрес. Отказ целиком — словами. `header` —
    непонятая первая строка файла — заголовок, а не замечание."""
    try:
        records = read_records(path, delimiter=delimiter)
    except OSError as exc:
        raise StoplistError(f"файл {path} не открылся: {exc.strerror or exc}") from exc
    except csv.Error as exc:
        raise StoplistError(f"файл {path.name} не читается как CSV: {exc}") from exc
    except ValueError as exc:  # не UTF-8 — уже словами
        raise StoplistError(str(exc)) from exc
    return _sorted(records, header=header)


def _sorted(records: list[tuple[int, list[str]]], *, header: bool) -> Entries:
    """Строки — по видам: домены и адреса без повторов, непонятые и бесплатная почта — с номером."""
    found: dict[str, dict[str, None]] = {"host": {}, "email": {}}
    unreadable: list[tuple[int, str]] = []
    free: list[tuple[int, str]] = []
    for number, (line, cells) in enumerate(records):
        cell = next((cell.strip() for cell in cells if cell.strip()), "")
        kind, value = _entry(cell)
        if kind in found:
            found[kind][value] = None
        elif kind == "free":
            free.append((line, cell))
        elif number or not header:  # первая нечитаемая строка файла — заголовок, не ошибка
            unreadable.append((line, cell))
    return Entries(list(found["host"]), list(found["email"]), unreadable, free)


def _entry(cell: str) -> tuple[str, str]:
    """Что в ячейке: («email», адрес), («host», корень домена), («free», домен бесплатной
    почты) или («», ячейка)."""
    email = "".join(cell.split()).lower()
    if "@" in email and len(email) <= EMAIL_LENGTH and EMAIL_RE.fullmatch(email):
        return "email", email
    if host := host_key(cell):
        return ("free", host) if host in FREE_MAILBOX_DOMAINS else ("host", host)
    return "", cell


async def add(
    session: AsyncSession,
    entries: Entries,
    *,
    author: str,
    note: str | None,
    author_id: int | None = None,
) -> Loaded:
    """Записать строки и снять лидов, которых они закрывают; повторы пропускаются молча и
    считаются. Коммит — за вызывающим.

    Журнал — одной записью на загрузку: она меняет, кому продажи напишут,
    и по журналу потом ищут, откуда взялся запрет.
    """
    if note is not None and len(note) > NOTE_LENGTH:
        raise StoplistError(f"причина длиннее {NOTE_LENGTH} знаков ({len(note)}) — сократите")
    added = 0
    for column, values in (("host", entries.hosts), ("email", entries.emails)):
        if values:
            added += await _insert(session, column, values, author=author, note=note)
    closed = await stoplist_leads.close(session) if added else 0
    if added:
        await AccessRepository(session).record(
            AuditAction.SUPPRESSION_ADDED,
            author_id=author_id,
            target="sales_stoplist",
            details={
                "источник": note or author,
                "доменов": len(entries.hosts),
                "адресов": len(entries.emails),
                "добавлено": added,
                "лидов снято": closed,
            },
        )
    logger.info("продажи: стоп-лист пополнен", extra={"added": added, "author": author})
    known = len(entries.hosts) + len(entries.emails) - added
    return Loaded(added, known, entries.unreadable, entries.free, closed)


async def rows(session: AsyncSession) -> list[SalesStoplistModel]:
    """Записи стоп-листа продаж, новые первыми. Список ведут люди — клиенты и партнёры, —
    с базой лидов он не растёт."""
    found = await session.scalars(
        select(SalesStoplistModel).order_by(
            SalesStoplistModel.created_at.desc(), SalesStoplistModel.id.desc()
        )
    )
    return list(found)


async def remove(session: AsyncSession, entry_id: int, *, author_id: int | None) -> Removed:
    """Снять запись и вернуть на очистку лидов, которых закрывала только она. Коммит — за
    вызывающим; журнал — здесь: снятие разрешает писать тем, кому было нельзя."""
    row = await session.get(SalesStoplistModel, entry_id)
    if row is None:
        raise UnknownEntryError(f"записи стоп-листа продаж №{entry_id} нет — обновите список")
    target = row.host or row.email or ""
    await session.delete(row)
    await session.flush()
    released = await stoplist_leads.release(session)
    await AccessRepository(session).record(
        AuditAction.SUPPRESSION_REMOVED,
        author_id=author_id,
        target=f"sales_stoplist:{entry_id}",
        details={"кому снова пишем": target, "лидов вернулось": released},
    )
    logger.info("продажи: запись стоп-листа снята", extra={"entry_id": entry_id})
    return Removed(target, released)


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
