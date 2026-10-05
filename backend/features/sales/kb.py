"""База знаний продаж: записи, что видит агент, версия базы.

**Агент пишет только из этих записей.** Выборку фактов делает одна функция —
`facts`: только включённые записи, сужение по виду, языку и тегам. Ею же экран
показывает «что увидит агент», и ею же считается версия: предпросмотр со своим
запросом мог бы показать не то, что получит агент.

**Версия базы — отпечаток содержимого, а не счётчик правок.** `kb-` и 12 знаков
sha256 от отсортированных записей «вид, язык, заголовок, текст, теги» — только
включённых. Пробелы сведены, порядок и номера записей не влияют: правка, вернувшая
текст как был, возвращает и версию, и по строке версии в черновике (срез 3.2) базу
можно узнать. Выключение записи версию меняет — агент её больше не видит.

**Правила записи — здесь, одни для экрана и консоли** (урок L63 соседнего проекта):
вид из набора, язык кодом, заголовок и текст непустые и в пределах, теги — метки
нижним регистром. Отказ — словами, что поправить. Журнал пишется тут же, в
транзакции правки, с версией до и после и прежними значениями правленых полей.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.sales.intake import LANGUAGE_CODE
from backend.features.sales.models import (
    LANGUAGE_LENGTH,
    TAG_LENGTH,
    TITLE_LENGTH,
    KbKind,
    SalesKbEntryModel,
)

logger = logging.getLogger(__name__)

#: Предел текста записи: факт, а не документ; бриф целиком — несколько страниц.
TEXT_LENGTH = 20_000
#: Тегов у записи: метки выборки, а не описание.
MAX_TAGS = 20
VERSION_PREFIX = "kb-"
#: Знаков отпечатка в версии: десятки версий за жизнь базы, совпадение исключено.
VERSION_DIGITS = 12
#: Поля записи — в порядке формы экрана и файла загрузки.
FIELDS = ("kind", "language", "title", "text", "tags", "active")


class KbError(ValueError):
    """Запись не годится. Текст — что поправить."""


class KbKeyTakenError(KbError):
    """Запись с тем же видом, языком и заголовком уже есть."""


class UnknownKbEntryError(LookupError):
    """Записи с таким номером нет."""


@dataclass(frozen=True, slots=True)
class Entry:
    """Запись, как её заводят и правят: поля уже приведены и проверены (`entry`)."""

    kind: KbKind
    language: str
    title: str
    text: str
    tags: tuple[str, ...] = ()
    active: bool = True

    @property
    def key(self) -> tuple[KbKind, str, str]:
        """По виду, языку и заголовку запись узнают экран и повторная загрузка."""
        return self.kind, self.language, self.title


@dataclass(frozen=True, slots=True)
class Fact:
    """Что агент видит в записи. Номер — чтобы черновик мог сослаться на факт."""

    id: int
    kind: KbKind
    language: str
    title: str
    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Group:
    """Факты одного вида на одном языке — так их показывает предпросмотр."""

    kind: KbKind
    language: str
    facts: list[Fact]


def _squash(value: str) -> str:
    return " ".join(value.split())


def _kind(value: str) -> KbKind:
    try:
        return KbKind(value)
    except ValueError:
        raise KbError(f"вида «{value}» нет; есть: {', '.join(KbKind)}") from None


def _language(value: str) -> str:
    code = value.strip().lower()
    if len(code) > LANGUAGE_LENGTH or not LANGUAGE_CODE.fullmatch(code):
        raise KbError(f"язык «{value}» — не код языка: ждём en, ru, pt-br")
    return code


def _title(value: str) -> str:
    title = _squash(value)
    if not title:
        raise KbError("нет заголовка — по нему запись узнают на экране и при загрузке файла")
    if len(title) > TITLE_LENGTH:
        raise KbError(f"заголовок длиннее {TITLE_LENGTH} знаков ({len(title)}) — нужен короткий")
    return title


def _text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise KbError("нет текста — пустая запись агенту ничего не скажет")
    if len(text) > TEXT_LENGTH:
        raise KbError(f"текст длиннее {TEXT_LENGTH} знаков ({len(text)}) — разбейте на записи")
    return text


def _tags(values: Iterable[str]) -> tuple[str, ...]:
    tags = sorted({_squash(value).lower() for value in values} - {""})
    if long := [tag for tag in tags if len(tag) > TAG_LENGTH]:
        raise KbError(f"тег длиннее {TAG_LENGTH} знаков: «{long[0][:40]}…» — тег короткая метка")
    if len(tags) > MAX_TAGS:
        raise KbError(f"тегов {len(tags)}, а можно не больше {MAX_TAGS}")
    return tuple(tags)


def entry(
    *,
    kind: str,
    language: str,
    title: str,
    text: str,
    tags: Iterable[str] = (),
    active: bool = True,
) -> Entry:
    """Поля записи → проверенная запись. Отказ — первой причиной, словами."""
    return Entry(
        kind=_kind(kind),
        language=_language(language),
        title=_title(title),
        text=_text(text),
        tags=_tags(tags),
        active=bool(active),
    )


def version_of(found: Iterable[Fact | Entry]) -> str:
    """Отпечаток того, что видит агент: порядок, номера и пробелы не влияют."""
    canon = sorted(
        [item.kind.value, item.language, _squash(item.title), _squash(item.text), sorted(item.tags)]
        for item in found
    )
    digest = hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode("utf-8")).hexdigest()
    return VERSION_PREFIX + digest[:VERSION_DIGITS]


#: Порядок записей: вид — в порядке перечисления (так сортирует тип базы), язык, заголовок.
_ORDER = (
    SalesKbEntryModel.kind,
    SalesKbEntryModel.language,
    SalesKbEntryModel.title,
    SalesKbEntryModel.id,
)


def _fact(row: SalesKbEntryModel) -> Fact:
    return Fact(row.id, row.kind, row.language, row.title, row.text, tuple(row.tags))


async def facts(
    session: AsyncSession,
    *,
    kinds: Iterable[KbKind] | None = None,
    language: str | None = None,
    tags: Iterable[str] | None = None,
) -> list[Fact]:
    """Что видит агент: только включённые записи, по виду, языку и заголовку.

    Сужение — по видам, языку и тегам (запись хоть с одним из тегов). База —
    десятки записей, выборки по виду и тегам хватает (план среза 3.2)."""
    query = select(SalesKbEntryModel).where(SalesKbEntryModel.active.is_(True))
    if kinds is not None:
        query = query.where(SalesKbEntryModel.kind.in_(list(kinds)))
    if language is not None:
        query = query.where(SalesKbEntryModel.language == language.strip().lower())
    if wanted := _tags(tags or ()):
        query = query.where(SalesKbEntryModel.tags.overlap(list(wanted)))
    rows = await session.scalars(query.order_by(*_ORDER))
    return [_fact(row) for row in rows]


async def version(session: AsyncSession) -> str:
    """Версия базы сейчас — её пишет черновик (срез 3.2)."""
    return version_of(await facts(session))


def grouped(found: Sequence[Fact]) -> list[Group]:
    """Факты группами по виду и языку: виды — в порядке набора, языки — по алфавиту."""
    groups: dict[tuple[KbKind, str], list[Fact]] = {}
    for fact in found:
        groups.setdefault((fact.kind, fact.language), []).append(fact)
    kinds = list(KbKind)
    order = sorted(groups, key=lambda key: (kinds.index(key[0]), key[1]))
    return [Group(kind, language, groups[kind, language]) for kind, language in order]


async def entries(session: AsyncSession) -> list[SalesKbEntryModel]:
    """Все записи — включённые и нет: экран показывает обе."""
    return list(await session.scalars(select(SalesKbEntryModel).order_by(*_ORDER)))


def _current(row: SalesKbEntryModel) -> dict[str, Any]:
    return {
        "kind": row.kind.value,
        "language": row.language,
        "title": row.title,
        "text": row.text,
        "tags": tuple(row.tags),
        "active": row.active,
    }


def _stored(value: object) -> object:
    """Значение поля — как его пишет журнал: вид кодом, теги списком."""
    if isinstance(value, KbKind):
        return value.value
    return list(value) if isinstance(value, tuple) else value


async def _flush(session: AsyncSession, row: SalesKbEntryModel) -> None:
    """Записать и прочитать назад: время правки ставит база (`onupdate=now()`), и без
    чтения поле истекает — экран в async читал бы его ленивым запросом и падал."""
    await session.flush()
    await session.refresh(row)


async def _key_free(session: AsyncSession, new: Entry, *, own: int | None = None) -> None:
    """Вторую запись с тем же ключом не заводим: отказ называет занятую."""
    query = select(SalesKbEntryModel.id).where(
        SalesKbEntryModel.kind == new.kind,
        SalesKbEntryModel.language == new.language,
        SalesKbEntryModel.title == new.title,
    )
    if own is not None:
        query = query.where(SalesKbEntryModel.id != own)
    taken = await session.scalar(query)
    if taken is not None:
        raise KbKeyTakenError(
            f"запись «{new.title}» ({new.kind.value}, {new.language}) уже есть — №{taken}: "
            "правьте её или назовите эту иначе"
        )


async def _journal(
    session: AsyncSession,
    row: SalesKbEntryModel,
    *,
    before: str,
    was: Mapping[str, object],
    author_id: int | None,
) -> None:
    after = await version(session)
    await AccessRepository(session).record(
        AuditAction.SALES_KB_CHANGED,
        author_id=author_id,
        target=f"sales_kb_entry:{row.id}",
        details={
            "вид": row.kind.value,
            "язык": row.language,
            "заголовок": row.title,
            "поля": list(was),
            "было": {name: _stored(value) for name, value in was.items()},
            "версия": {"было": before, "стало": after},
        },
    )
    logger.info(
        "продажи: база знаний изменена",
        extra={"entry_id": row.id, "fields": list(was), "version": after},
    )


async def add(
    session: AsyncSession, new: Entry, *, author: str, author_id: int | None
) -> SalesKbEntryModel:
    """Завести запись. Запись и журнал — в транзакции вызывающего, коммит — за ним."""
    await _key_free(session, new)
    before = await version(session)
    values = asdict(new)
    row = SalesKbEntryModel(**{**values, "tags": list(new.tags)}, updated_by=author)
    session.add(row)
    await _flush(session, row)
    await _journal(session, row, before=before, was=dict.fromkeys(FIELDS), author_id=author_id)
    return row


async def _row(session: AsyncSession, entry_id: int) -> SalesKbEntryModel:
    row = await session.get(SalesKbEntryModel, entry_id)
    if row is None:
        raise UnknownKbEntryError(f"записи базы знаний №{entry_id} нет — обновите список")
    return row


async def change(
    session: AsyncSession,
    entry_id: int,
    changes: Mapping[str, Any],
    *,
    author: str,
    author_id: int | None,
) -> SalesKbEntryModel:
    """Поправить запись: любые поля, включение — тоже правка. Без изменений — без журнала."""
    row = await _row(session, entry_id)
    current = _current(row)
    merged = entry(**{**current, **changes})
    fresh = {name: getattr(merged, name) for name in FIELDS}
    was = {name: current[name] for name in FIELDS if _stored(fresh[name]) != _stored(current[name])}
    if not was:
        return row
    if merged.key != (row.kind, row.language, row.title):
        await _key_free(session, merged, own=row.id)
    before = await version(session)
    for name in was:
        setattr(row, name, list(merged.tags) if name == "tags" else fresh[name])
    row.updated_by = author
    await _flush(session, row)
    await _journal(session, row, before=before, was=was, author_id=author_id)
    return row
