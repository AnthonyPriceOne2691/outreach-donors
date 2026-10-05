"""База знаний продаж на настоящей базе — срез 3.1: версия, что видит агент, правка, журнал, схема.

Тексты записей заведомо выдуманные: студия примеров для тестов, цена «после созвона».
База настоящая: запись, ключ «вид, язык, заголовок», выборка фактов и журнал — общий
`audit_log`. Утверждения точные: испорченное правило версии или выборки краснеет.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import kb
from backend.features.sales.kb import Entry, KbError, KbKeyTakenError, UnknownKbEntryError, entry
from backend.features.sales.models import KbKind, SalesKbEntryModel
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "backend/migrations/versions/c6efe4e5de7e_sales_kb_and_settings.py"
JOURNAL = ROOT / "backend/migrations/versions/a51e5688f79e_sales_kb_changed_audit_action.py"
SCHEMA = frozenset({"sales_kb_entries", "sales_settings", "sales_kb_kind"})

BRIEF = entry(
    kind="brief",
    language="ru",
    title="Кто мы",
    text="Студия выдуманных сайтов: делаем примеры для тестов.",
)
PRICE = entry(
    kind="price_policy",
    language="ru",
    title="Цена аудита",
    text="Цену называем после короткого созвона.",
    tags=["цена", "аудит"],
)
CASE = entry(
    kind="case",
    language="en",
    title="Made-up shop",
    text="Doubled the traffic of a made-up test shop.",
    tags=["seo"],
)


async def _add(session: AsyncSession, *entries: Entry) -> list[SalesKbEntryModel]:
    return [await kb.add(session, item, author="тест", author_id=None) for item in entries]


async def _journal(session: AsyncSession) -> list[tuple[str | None, dict[str, Any] | None]]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_KB_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [(row.target, row.details) for row in rows]


def _titles(found: list[kb.Fact]) -> list[str]:
    return [fact.title for fact in found]


# --- A1: версия базы ----------------------------------------------------------------


async def test_a1_price_edit_changes_the_version_and_the_old_one_still_names_the_old_base(
    session: AsyncSession,
) -> None:
    # A1 — пример спеки
    """Черновик хранит строку версии (срез 3.2). Правка цены даёт новую версию, а
    прежняя строка остаётся отпечатком прежней базы: правка, вернувшая текст, возвращает
    ровно её — по версии в старом черновике базу можно узнать."""
    _, price = await _add(session, BRIEF, PRICE)
    kept = await kb.version(session)  # так её запомнит черновик

    await kb.change(
        session,
        price.id,
        {"text": "Цену называем сразу: от выдуманных 137."},
        author="т",
        author_id=None,
    )
    edited = await kb.version(session)
    await kb.change(session, price.id, {"text": PRICE.text}, author="т", author_id=None)

    assert edited != kept
    assert await kb.version(session) == kept
    versions = [details["версия"] for _, details in await _journal(session) if details][-2:]
    assert versions == [{"было": kept, "стало": edited}, {"было": edited, "стало": kept}]


def test_a1_order_numbers_and_whitespace_do_not_change_the_version() -> None:
    # A1 — пример спеки
    spaced = entry(
        kind="price_policy",
        language=" RU ",
        title="  Цена   аудита ",
        text="Цену называем\n\nпосле   короткого созвона.  ",
        tags=["Аудит", " цена ", "цена"],
    )
    fact = kb.Fact(
        7, PRICE.kind, PRICE.language, " Цена\tаудита", PRICE.text + "\n", ("цена", "аудит")
    )

    assert kb.version_of([BRIEF, PRICE]) == kb.version_of([PRICE, BRIEF])
    assert kb.version_of([BRIEF, spaced]) == kb.version_of([BRIEF, PRICE])
    assert kb.version_of([fact]) == kb.version_of([PRICE])


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "Цену называем после длинного созвона."},
        {"title": "Цена аудита сайта"},
        {"tags": ["цена"]},
        {"kind": "objection"},
        {"language": "en"},
        {"active": False},
    ],
)
def test_a1_each_field_the_agent_reads_changes_the_version(changes: dict[str, Any]) -> None:
    # A1 — пример спеки
    fields = {name: getattr(PRICE, name) for name in kb.FIELDS} | changes
    edited = entry(**fields)
    seen = [item for item in (BRIEF, edited) if item.active]

    assert kb.version_of(seen) != kb.version_of([BRIEF, PRICE])


def test_a1_version_is_kb_and_twelve_hex_digits_of_sha256() -> None:
    # A1 — пример спеки
    empty = hashlib.sha256(b"[]").hexdigest()[:12]

    assert kb.version_of([]) == f"kb-{empty}"
    assert len(kb.version_of([PRICE])) == len("kb-") + 12


async def test_a1_version_is_content_not_row_numbers(session: AsyncSession) -> None:
    # A1 — пример спеки
    """Запись заведена заново с тем же содержимым — номер другой, версия та же."""
    _, price = await _add(session, BRIEF, PRICE)
    before = await kb.version(session)

    await session.execute(delete(SalesKbEntryModel).where(SalesKbEntryModel.id == price.id))
    (again,) = await _add(session, PRICE)

    assert again.id != price.id
    assert await kb.version(session) == before == kb.version_of([BRIEF, PRICE])


# --- A2: что видит агент -----------------------------------------------------------


async def test_a2_switched_off_entry_is_not_seen_by_the_agent_by_any_selection(
    session: AsyncSession,
) -> None:
    # A2 — пример спеки
    _, price, _ = await _add(session, BRIEF, PRICE, CASE)

    await kb.change(session, price.id, {"active": False}, author="т", author_id=None)

    assert _titles(await kb.facts(session)) == ["Кто мы", "Made-up shop"]
    assert await kb.facts(session, kinds=[KbKind.PRICE_POLICY]) == []
    assert await kb.facts(session, tags=["цена"]) == []
    assert _titles(await kb.facts(session, language="ru")) == ["Кто мы"]
    assert await kb.version(session) == kb.version_of([BRIEF, CASE])
    groups = kb.grouped(await kb.facts(session))
    assert [(group.kind, group.language) for group in groups] == [
        (KbKind.BRIEF, "ru"),
        (KbKind.CASE, "en"),
    ]
    # Список экрана видит и выключенную: её включают обратно оттуда.
    assert [row.title for row in await kb.entries(session)] == [
        "Кто мы",
        "Made-up shop",
        "Цена аудита",
    ]


async def test_a2_switched_back_on_entry_is_seen_again(session: AsyncSession) -> None:
    # A2 — пример спеки
    _, price = await _add(session, BRIEF, PRICE)
    both = await kb.version(session)

    await kb.change(session, price.id, {"active": False}, author="т", author_id=None)
    await kb.change(session, price.id, {"active": True}, author="т", author_id=None)

    assert _titles(await kb.facts(session, kinds=[KbKind.PRICE_POLICY])) == ["Цена аудита"]
    assert await kb.version(session) == both


async def test_facts_narrow_by_kinds_language_and_any_of_the_tags(session: AsyncSession) -> None:
    await _add(session, BRIEF, PRICE, CASE)

    assert _titles(await kb.facts(session, kinds=[KbKind.CASE, KbKind.BRIEF])) == [
        "Кто мы",
        "Made-up shop",
    ]
    assert _titles(await kb.facts(session, language=" EN ")) == ["Made-up shop"]
    assert _titles(await kb.facts(session, tags=["SEO", "нет такого"])) == ["Made-up shop"]
    assert _titles(await kb.facts(session, tags=[])) == ["Кто мы", "Made-up shop", "Цена аудита"]
    assert await kb.facts(session, kinds=[]) == []


def test_groups_follow_the_kind_order_and_languages_alphabetically() -> None:
    def fact(kind: KbKind, language: str) -> kb.Fact:
        return kb.Fact(1, kind, language, "т", "т", ())

    found = [fact(KbKind.CTA, "en"), fact(KbKind.BRIEF, "ru"), fact(KbKind.BRIEF, "en")]

    assert [(g.kind, g.language) for g in kb.grouped(found)] == [
        (KbKind.BRIEF, "en"),
        (KbKind.BRIEF, "ru"),
        (KbKind.CTA, "en"),
    ]


# --- правила записи ------------------------------------------------------------------


def test_fields_are_brought_to_one_form() -> None:
    found = entry(
        kind=KbKind.CTA,
        language="PT-BR",
        title="  Звать   на созвон ",
        text="\r\nПредложить созвон\r\nна выдуманные 20 минут.\n",
        tags=["Созвон", "созвон ", "", "  b2b  "],
        active=False,
    )

    assert found == Entry(
        kind=KbKind.CTA,
        language="pt-br",
        title="Звать на созвон",
        text="Предложить созвон\nна выдуманные 20 минут.",
        tags=("b2b", "созвон"),
        active=False,
    )


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        ({"kind": "pricing"}, "вида «pricing» нет; есть: brief, service, case, objection, "),
        ({"language": "Russian"}, "язык «Russian» — не код языка: ждём en, ru, pt-br"),
        ({"title": "  "}, "нет заголовка — по нему запись узнают"),
        ({"title": "я" * 256}, "заголовок длиннее 255 знаков (256)"),
        ({"text": " \n "}, "нет текста — пустая запись агенту ничего не скажет"),
        ({"text": "я" * 20_001}, "текст длиннее 20000 знаков (20001) — разбейте на записи"),
        ({"tags": ["я" * 65]}, "тег длиннее 64 знаков"),
        ({"tags": [f"тег{n}" for n in range(21)]}, "тегов 21, а можно не больше 20"),
    ],
)
def test_bad_fields_are_refused_in_words(fields: dict[str, Any], words: str) -> None:
    good = {"kind": "brief", "language": "ru", "title": "Кто мы", "text": "Студия примеров."}

    with pytest.raises(KbError) as refused:
        entry(**(good | fields))

    assert str(refused.value).startswith(words)


async def test_second_entry_with_the_same_kind_language_and_title_is_refused(
    session: AsyncSession,
) -> None:
    (price,) = await _add(session, PRICE)
    twin = entry(kind="price_policy", language="ru", title=" Цена  аудита", text="Другой текст.")

    with pytest.raises(KbKeyTakenError) as refused:
        await kb.add(session, twin, author="т", author_id=None)

    assert str(refused.value) == (
        f"запись «Цена аудита» (price_policy, ru) уже есть — №{price.id}: "
        "правьте её или назовите эту иначе"
    )
    # Тот же заголовок другого вида или языка — другая запись.
    await kb.add(session, entry(**(PRICE_FIELDS | {"language": "en"})), author="т", author_id=None)
    assert len(await kb.entries(session)) == 2


PRICE_FIELDS = {name: getattr(PRICE, name) for name in kb.FIELDS}


async def test_renaming_into_a_taken_key_is_refused_and_own_key_is_kept(
    session: AsyncSession,
) -> None:
    brief, price = await _add(session, BRIEF, PRICE)

    with pytest.raises(KbKeyTakenError, match=f"уже есть — №{brief.id}"):
        await kb.change(
            session, price.id, {"kind": "brief", "title": "Кто мы"}, author="т", author_id=None
        )
    edited = await kb.change(
        session,
        price.id,
        {"title": "Цена аудита", "text": "Новый текст."},
        author="т",
        author_id=None,
    )

    assert (edited.title, edited.text) == ("Цена аудита", "Новый текст.")


async def test_the_database_keeps_the_key_unique_as_the_last_line(session: AsyncSession) -> None:
    await _add(session, PRICE)
    twin = SalesKbEntryModel(**(PRICE_FIELDS | {"tags": [], "text": "другой"}))

    with pytest.raises(IntegrityError, match="uq_sales_kb_entries_key"):
        async with session.begin_nested():
            session.add(twin)
            await session.flush()


async def test_unknown_entry_is_refused_in_words(session: AsyncSession) -> None:
    with pytest.raises(UnknownKbEntryError) as refused:
        await kb.change(session, 999_999, {"active": False}, author="т", author_id=None)

    assert str(refused.value) == "записи базы знаний №999999 нет — обновите список"


async def test_bad_change_is_refused_and_the_entry_stays_as_it_was(session: AsyncSession) -> None:
    (price,) = await _add(session, PRICE)

    with pytest.raises(KbError, match="нет текста"):
        await kb.change(session, price.id, {"text": "  "}, author="т", author_id=None)

    assert (price.text, len(await _journal(session))) == (PRICE.text, 1)


# --- журнал и кто правил ----------------------------------------------------------------


async def test_adding_writes_the_author_and_one_journal_line_with_both_versions(
    session: AsyncSession,
) -> None:
    empty = await kb.version(session)

    row = await kb.add(session, PRICE, author="seller@ours.example.test", author_id=None)

    assert (row.updated_by, row.tags, row.active) == (
        "seller@ours.example.test",
        ["аудит", "цена"],
        True,
    )
    assert (row.created_at is not None, row.updated_at is not None) == (True, True)
    assert await _journal(session) == [
        (
            f"sales_kb_entry:{row.id}",
            {
                "вид": "price_policy",
                "язык": "ru",
                "заголовок": "Цена аудита",
                "поля": list(kb.FIELDS),
                "было": dict.fromkeys(kb.FIELDS),
                "версия": {"было": empty, "стало": kb.version_of([PRICE])},
            },
        )
    ]


async def test_change_journals_only_the_changed_fields_with_their_old_values(
    session: AsyncSession,
) -> None:
    (price,) = await _add(session, PRICE)

    edited = await kb.change(
        session,
        price.id,
        {"tags": ["цена"], "text": PRICE.text, "active": False},
        author="admin@ours.example.test",
        author_id=None,
    )

    # Время правки прочитано из базы сразу: ленивое чтение в async упало бы.
    assert (edited.updated_by, edited.updated_at is not None) == ("admin@ours.example.test", True)
    _, details = (await _journal(session))[-1]
    assert details is not None
    assert (details["поля"], details["было"]) == (
        ["tags", "active"],
        {"tags": ["аудит", "цена"], "active": True},
    )


async def test_change_without_a_difference_writes_nothing(session: AsyncSession) -> None:
    (price,) = await _add(session, PRICE)

    same = await kb.change(
        session,
        price.id,
        {"title": " Цена аудита ", "tags": ["цена", "аудит"]},
        author="другой",
        author_id=None,
    )

    assert (same.updated_by, len(await _journal(session))) == ("тест", 1)


# --- схема ---------------------------------------------------------------------------------


def _migration(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"sales_kb_migration_{path.stem}", path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _present(connection: Connection) -> set[str]:
    """Таблицы и тип вида записи — что из добавленного миграцией есть сейчас."""
    tables = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:names)"),
        {"names": sorted(SCHEMA)},
    )
    types = connection.execute(text("SELECT typname FROM pg_type WHERE typname = 'sales_kb_kind'"))
    return set(tables.scalars()) | set(types.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    migration = _migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        after_downgrade = _present(connection)
        migration.upgrade()
    return after_downgrade, _present(connection)


async def test_migration_creates_both_tables_and_the_kind_type_and_takes_them_back(
    session: AsyncSession,
) -> None:
    """Урок L5: тип перечисления таблица с собой не уносит — без `DROP TYPE` в откате
    повторный подъём падает на «тип уже существует». Цикл — на соединении теста
    (DDL в Postgres транзакционный — откат теста вернёт схему)."""
    connection = await session.connection()
    assert await connection.run_sync(_present) == SCHEMA

    after_downgrade, after_upgrade = await connection.run_sync(_down_and_up)
    assert (after_downgrade, after_upgrade) == (set(), SCHEMA)


def _journal_values(connection: Connection) -> list[str]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие
    его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = _migration(JOURNAL)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_journal_value_is_there_once_and_survives_a_rerun(session: AsyncSession) -> None:
    connection = await session.connection()
    assert (await connection.run_sync(_journal_values)).count("sales_kb_changed") == 1
