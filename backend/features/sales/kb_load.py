"""Загрузка базы знаний из файла — первичное наполнение из брифа.

**Файл — JSON: список записей** `{"kind", "language", "title", "text", "tags",
"active"}`; последние два необязательны. Не CSV: текст записи — абзацы, и в CSV
их держали бы в кавычках со сдвоенными кавычками внутри; файл из брифа собирают
программой или в редакторе, и JSON их не ломает.

**Файл лежит вне репозитория.** Факты компании — коммерческий текст, а репозиторий
публичный: файл внутри копии репозитория не читается вовсе — одна команда
`git add .` унесла бы его в историю навсегда.

**Всё или ничего.** База — десятки записей, их правят по отчёту и грузят заново:
частично загруженная база — это агент, который пишет по половине фактов. Ошибка
хотя бы в одной записи — отказ с номером записи и словами, в базе ничего не меняется.

**Повтор не задваивает.** Запись узнаётся по ключу «вид, язык, заголовок». Такая же —
«без изменений»; с другим текстом, тегами или включением — «отличается» и без
`--update` не трогается: её могли поправить на экране после первой загрузки, а старый
файл молча откатил бы правку. Записи базы, которых нет в файле, не трогаются тоже.
Журнал — одной записью на загрузку, с версией базы до и после.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.sales import kb, outside
from backend.features.sales.kb import Entry, KbError
from backend.features.sales.models import SalesKbEntryModel

logger = logging.getLogger(__name__)

#: Больше файл базы знаний не бывает: десятки записей по несколько абзацев.
MAX_BYTES = 2 * 1024 * 1024
REQUIRED = ("kind", "language", "title", "text")


class KbFileError(ValueError):
    """Файл не читается целиком. Текст — что делать."""


@dataclass(frozen=True, slots=True)
class Problem:
    """Запись с ошибкой: номер в файле с единицы, заголовок, если есть, и причина."""

    number: int
    title: str | None
    reason: str


#: Что станет с базой: по ключу записи — новая, такая же или отличается.
type Plan = outside.Sorting[Entry, SalesKbEntryModel]


@dataclass(frozen=True, slots=True)
class Loaded:
    added: int
    updated: int
    before: str
    after: str


def _decoded(path: Path) -> Any:
    try:
        return outside.read_json(
            path, leaks="факты компании", too_big="это не база знаний", max_bytes=MAX_BYTES
        )
    except outside.OutsideFileError as exc:
        raise KbFileError(str(exc)) from exc


def _problem(item: Any) -> str | None:
    """Чего не хватает записи по форме — до правил ядра."""
    if not isinstance(item, dict):
        return 'не запись: ждём объект {"kind": …, "language": …, "title": …, "text": …}'
    return _keys_problem(item) or _types_problem(item)


def _keys_problem(item: dict[str, Any]) -> str | None:
    if extra := sorted(set(item) - set(kb.FIELDS)):
        return f"лишние поля {', '.join(extra)}: ждём {', '.join(kb.FIELDS)}"
    missing = [name for name in REQUIRED if name not in item]
    return f"нет полей {', '.join(missing)}" if missing else None


def _types_problem(item: dict[str, Any]) -> str | None:
    if any(not isinstance(item[name], str) for name in REQUIRED):
        return "вид, язык, заголовок и текст — строки"
    tags = item.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return "теги — список строк"
    return None if isinstance(item.get("active", True), bool) else "включена — true или false"


def _entry(item: Any, seen: dict[tuple[Any, ...], int], number: int) -> Entry | Problem:
    title = item.get("title") if isinstance(item, dict) else None
    named = title if isinstance(title, str) else None
    if (reason := _problem(item)) is not None:
        return Problem(number, named, reason)
    try:
        found = kb.entry(**item)
    except KbError as exc:
        logger.info("продажи: запись файла базы знаний не годится", extra={"number": number})
        return Problem(number, named, str(exc))
    if (twin := seen.setdefault(found.key, number)) != number:
        return Problem(
            number, found.title, f"та же запись, что №{twin}: вид, язык и заголовок совпадают"
        )
    return found


def read(path: Path) -> tuple[list[Entry], list[Problem]]:
    """Файл → записи и ошибки по номерам. Отказ целиком — `KbFileError`."""
    data = _decoded(path)
    if not isinstance(data, list) or not data:
        raise KbFileError(f"в файле {path.name} ждём непустой список записей: [{{…}}, {{…}}]")
    seen: dict[tuple[Any, ...], int] = {}
    entries: list[Entry] = []
    problems: list[Problem] = []
    for number, item in enumerate(data, start=1):
        found = _entry(item, seen, number)
        if isinstance(found, Problem):
            problems.append(found)
        else:
            entries.append(found)
    return entries, problems


def _same(new: Entry, row: SalesKbEntryModel) -> bool:
    return (new.text, list(new.tags), new.active) == (row.text, list(row.tags), row.active)


async def plan(session: AsyncSession, entries: Sequence[Entry]) -> Plan:
    """Сверка файла с базой по ключу записи. Ничего не пишет."""
    rows = {(row.kind, row.language, row.title): row for row in await kb.entries(session)}
    return outside.sort_against(entries, rows, key=lambda new: new.key, same=_same)


async def apply(
    session: AsyncSession, planned: Plan, *, update: bool, author: str, source: str
) -> Loaded:
    """Записать новое и — с `update` — отличающееся. Журнал одной записью. Коммит — за вызывающим."""
    before = await kb.version(session)
    if planned.added:
        rows = [
            asdict(new) | {"tags": list(new.tags), "updated_by": author} for new in planned.added
        ]
        await session.execute(insert(SalesKbEntryModel), rows)
    updated = planned.differs if update else []
    for new, row in updated:
        row.text, row.tags, row.active, row.updated_by = (
            new.text,
            list(new.tags),
            new.active,
            author,
        )
    await session.flush()
    after = await kb.version(session)
    if planned.added or updated:
        await AccessRepository(session).record(
            AuditAction.SALES_KB_CHANGED,
            target="sales_kb",
            details={
                "источник": source,
                "добавлено": len(planned.added),
                "обновлено": len(updated),
                "без изменений": len(planned.same),
                "отличается, не тронуто": len(planned.differs) - len(updated),
                "версия": {"было": before, "стало": after},
            },
        )
    logger.info("продажи: база знаний загружена", extra={"source": source, "version": after})
    return Loaded(len(planned.added), len(updated), before, after)
