"""Цепочка писем продаж на настоящей базе — срез 4.6, часть 1: правила, версия, набор, журнал.

Тексты шаблонов заведомо выдуманные («Hello {{name}}, test body»): репозиторий публичный,
коммерческих текстов в нём нет. База настоящая: шаблоны, ключ «набор, шаг, язык»,
выбор цепочки гипотезы и журнал — общий `audit_log`. Утверждения точные: испорченное
правило темы, подстановок, метрик или версии краснеет.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.template import ZoneKind
from backend.features.sales import chain, chain_text, hypotheses, sender
from backend.features.sales.chain import ChainNotReadyError
from backend.features.sales.chain_text import ChainError, StepTemplate, step_template
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesChainTemplateModel
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "backend/migrations/versions/723e3ddab31f_sales_chain_templates.py"
JOURNAL = ROOT / "backend/migrations/versions/cbc5aadf4fc2_sales_chain_changed_audit_action.py"

FIRST_BODY = (
    "[greeting] rewrite\nHello {{name}},\n\n"
    "[opening] rewrite\nThis is a made-up test opening about {{site}} for {{company}} only.\n\n"
    "[offer] fixed\nTest offer: nothing real is sold here."
)
FOLLOW_BODY = "[reminder] fixed\nJust a made-up test reminder, {{name}}."
SUBJECT = "Test question for {{company}}"
#: Первое письмо без первой зоны — чтобы собрать его заново с другими пробелами.
REST = FIRST_BODY.split("\n\n", 1)[1]


def first(language: str = "en", **changes: Any) -> StepTemplate:
    fields: dict[str, Any] = {"step": 1, "language": language, "subject": SUBJECT}
    return step_template(**(fields | {"body": FIRST_BODY} | changes))


def follow(step: int = 2, language: str = "en", **changes: Any) -> StepTemplate:
    return step_template(**({"step": step, "language": language, "body": FOLLOW_BODY} | changes))


async def _save(
    session: AsyncSession, *templates: StepTemplate, hypothesis_id: int | None = None
) -> list[SalesChainTemplateModel]:
    return [
        await chain.save(session, new, hypothesis_id=hypothesis_id, author="тест", author_id=None)
        for new in templates
    ]


async def _journal(session: AsyncSession) -> list[tuple[str | None, dict[str, Any] | None]]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_CHAIN_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [(row.target, row.details) for row in rows]


async def _hypothesis(session: AsyncSession, name: str = "Тестовая гипотеза") -> int:
    return (await hypotheses.add(session, name, None)).id


# --- правила записи ---------------------------------------------------------------------


def test_first_letter_is_brought_to_one_form() -> None:
    made = step_template(
        step=1,
        language=" EN ",
        subject="  Test   question\nfor {{company}} ",
        body="\r\n[greeting] rewrite\r\nHello {{name}},  \r\n\r\n\r\n" + REST + "\n\n",
    )

    assert (made.step, made.language, made.subject) == (1, "en", "Test question for {{company}}")
    assert made.body == FIRST_BODY
    assert [(zone.name, zone.kind) for zone in made.zones] == [
        ("greeting", ZoneKind.REWRITE),
        ("opening", ZoneKind.REWRITE),
        ("offer", ZoneKind.FIXED),
    ]


@pytest.mark.parametrize("subject", ["Re: test", "RE:test", "Re[2]: test", "Fwd: test", "FW: test"])
def test_first_letter_subject_never_pretends_to_be_a_reply(subject: str) -> None:
    # Мутант: без проверки приставки тема «Re:» проходила бы.
    with pytest.raises(ChainError) as refused:
        first(subject=subject)

    prefix = subject.split(":", maxsplit=1)[0] + ":"
    assert f"начинается с «{prefix}»" in str(refused.value)
    assert "обман адресата" in str(refused.value)


@pytest.mark.parametrize("subject", ["Отв: тест", "ответ:тест"])
def test_russian_reply_prefix_is_refused_too(subject: str) -> None:
    with pytest.raises(ChainError, match="выдаёт первое письмо за ответ или пересылку"):
        first(language="ru", subject=subject)


@pytest.mark.parametrize("subject", ["Regarding {{site}}", "Ответ на ваш вопрос", "Fwding tips"])
def test_subject_that_only_starts_like_a_prefix_is_fine(subject: str) -> None:
    assert first(subject=subject).subject == subject


@pytest.mark.parametrize("subject", [None, "", "   \n "])
def test_first_letter_needs_a_subject(subject: str | None) -> None:
    with pytest.raises(ChainError, match="у первого письма нет темы"):
        first(subject=subject)


def test_subject_longer_than_the_column_is_refused() -> None:
    with pytest.raises(ChainError, match="тема длиннее 255 знаков"):
        first(subject="t" * 256)


def test_followup_has_no_subject_it_goes_in_the_same_thread() -> None:
    assert follow().subject is None
    assert follow(step=3, subject="  ").subject is None
    with pytest.raises(ChainError, match="у добивки темы нет — она уходит в той же переписке"):
        follow(subject="Test reminder")


def test_followup_zones_are_fixed_the_model_does_not_rewrite_them() -> None:
    with pytest.raises(ChainError, match="добивку модель не переписывает"):
        follow(body="[reminder] rewrite\nJust a made-up test reminder.")


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"step": 0}, "шага 0 нет: 1 — первое письмо, 2 и 3 — добивки"),
        ({"step": 4}, "шага 4 нет"),
        ({"language": "de"}, "языка «de» у цепочки нет; есть: ru, en"),
        ({"body": "   \n "}, "нет текста письма"),
        ({"body": "[offer] fixed\n" + "x" * 10_000}, "текст длиннее 10000 знаков"),
    ],
)
def test_step_language_and_size_are_refused_in_words(changes: dict[str, Any], words: str) -> None:
    with pytest.raises(ChainError) as refused:
        first(**changes)

    assert words in str(refused.value)


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ("Hello\n" + FIRST_BODY, "строка 1: текст до первой зоны — «Hello»"),
        (FIRST_BODY + "\n# note", "строка 9 начинается с «#»"),
        (FIRST_BODY + "\n\n[Offer] fixed\nTest", "строка 10 «[Offer] fixed» похожа на заголовок"),
        (FIRST_BODY + "\n [cta] fixed\nTest", "строка 9 «[cta] fixed» похожа на заголовок"),
        (FIRST_BODY + "\n\n[offer] fixed\nAgain", "строка 10: зона «offer» уже есть"),
        (FIRST_BODY + "\n\n[cta] fixed\n\n", "зона «cta» пуста — в письме пропал бы абзац"),
        (FIRST_BODY + "\n\n[cta] other\nTest", "строка 10: вида зоны «other» нет"),
        ("no zones at all", "строка 1: текст до первой зоны"),
    ],
)
def test_body_lines_the_parse_does_not_understand_are_refused(body: str, words: str) -> None:
    with pytest.raises(ChainError) as refused:
        first(body=body)

    assert words in str(refused.value)


def test_first_letter_must_be_able_to_reach_the_difference_corridor() -> None:
    fixed_only = "[offer] fixed\nTest offer: nothing real is sold here, only made-up words."
    tiny = "[greeting] rewrite\nHi,\n\n" + fixed_only

    for body in (fixed_only, tiny):
        with pytest.raises(ChainError, match=r"нижний край коридора отличия \(15%\) недостижим"):
            first(body=body)


# --- подстановки, метрики, подпись ------------------------------------------------------


@pytest.mark.parametrize(
    "typo", ["{{nam}}", "{{Name}}", "{{ name }}", "{name}", "{{name}", "name}}", "{{first_name}}"]
)
def test_unknown_or_broken_placeholder_is_refused_in_words(typo: str) -> None:
    # Мутант: без проверки подстановок опечатка ушла бы адресату как есть.
    with pytest.raises(ChainError) as refused:
        first(body=FIRST_BODY.replace("{{name}}", typo))

    assert "{{name}}, {{company}}, {{site}}" in str(refused.value)


def test_unknown_placeholder_in_the_subject_is_refused_too() -> None:
    with pytest.raises(ChainError, match=r"подстановки \{\{host\}\} нет"):
        first(subject="Test for {{host}}")


@pytest.mark.parametrize(
    "text", ["Our DR 45 test", "made-up organic traffic", "Ahrefs says", "Domain Rating test"]
)
def test_ahrefs_metrics_never_get_into_the_template(text: str) -> None:
    # Мутант: без проверки метрик «DR 45» ушло бы в каждое письмо — это стоит ключа.
    with pytest.raises(ForbiddenContentError, match="метрики Ahrefs"):
        first(body=FIRST_BODY + f" {text}")
    with pytest.raises(ForbiddenContentError, match="метрики Ahrefs"):
        first(subject=f"Test: {text}")


@pytest.mark.parametrize(
    "name", ["sender_name", "signature", "postal_address", "physical_address", "unsubscribe_url"]
)
def test_signature_and_address_placeholders_are_left_to_the_assembly(name: str) -> None:
    with pytest.raises(ChainError, match="допишет сборка из настроек отправителя"):
        first(body=FIRST_BODY + f"\n{{{{{name}}}}}")


@pytest.mark.parametrize("zone", ["signature", "address"])
def test_signature_and_address_zones_are_left_to_the_assembly(zone: str) -> None:
    with pytest.raises(ChainError, match=f"зоны «{zone}» у шаблона продаж нет"):
        first(body=FIRST_BODY + f"\n\n[{zone}] fixed\nTest")


def _sender(**values: str) -> sender.Sender:
    return sender.Sender(dict.fromkeys(sender.FIELDS) | values)


@pytest.mark.parametrize(
    ("field", "value", "words"),
    [
        ("signature", "Iva Testova\nTest lead, Example Studio", "подпись из настроек"),
        ("physical_address", "1 Test Street,\nTestville", "физический адрес из настроек"),
    ],
)
def test_settings_signature_or_address_inside_the_body_is_refused(
    field: str, value: str, words: str
) -> None:
    inside = first(body=FIRST_BODY + "\n" + " ".join(value.split()).upper())

    with pytest.raises(
        ChainError, match=f"в тексте — {words} отправителя: сборка допишет это сама"
    ):
        chain_text.unsigned(inside, _sender(**{field: value}))
    chain_text.unsigned(inside, _sender())  # настройки пусты — сверять не с чем


# --- версия -------------------------------------------------------------------------------


def test_version_is_chain_and_twelve_hex_digits_of_sha256() -> None:
    canon = [[1, "en", "Test question for {{company}}", FIRST_BODY]]
    digest = hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode()).hexdigest()

    assert chain.version_of([first()]) == "chain-" + digest[:12]


def test_order_and_switched_off_steps_do_not_shape_the_version() -> None:
    off = follow(step=3, active=False)

    assert chain.version_of([first(), follow()]) == chain.version_of([follow(), first()])
    assert chain.version_of([first(), off]) == chain.version_of([first()])


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "Another test question"},
        {"body": FIRST_BODY.replace("nothing real", "nothing true")},
        {"language": "ru"},
        {"active": False},
    ],
)
def test_each_field_of_a_step_changes_the_version(changes: dict[str, Any]) -> None:
    # Мутант: версия без темы или тела не менялась бы при правке текста.
    assert chain.version_of([first(**changes)]) != chain.version_of([first()])


async def test_edit_changes_the_version_and_reverting_the_edit_returns_it(
    session: AsyncSession,
) -> None:
    await _save(session, first(), follow())
    kept = await chain.set_version(session, None, "en")

    edited_body = FIRST_BODY.replace("only.", "and nothing more.")
    await _save(session, first(body=edited_body))
    edited = await chain.set_version(session, None, "en")
    await _save(session, first())

    assert edited != kept
    assert await chain.set_version(session, None, "en") == kept
    versions = [details["версия"] for _, details in await _journal(session) if details][-2:]
    assert versions == [{"было": kept, "стало": edited}, {"было": edited, "стало": kept}]


# --- набор и гипотеза ---------------------------------------------------------------------


async def test_common_chain_serves_a_hypothesis_without_its_own_steps(
    session: AsyncSession,
) -> None:
    hypothesis_id = await _hypothesis(session)
    await _save(session, first(), follow(), follow(3))

    found = await chain.resolve(session, hypothesis_id=hypothesis_id, language="EN")

    assert (found.hypothesis_id, found.language, sorted(found.steps)) == (None, "en", [1, 2, 3])
    assert found.missing == []
    found.check_ready()


async def test_own_chain_replaces_the_common_one_whole_steps_never_mix(
    session: AsyncSession,
) -> None:
    hypothesis_id = await _hypothesis(session)
    await _save(session, first(), follow(), follow(3))
    await _save(session, first(subject="Own test question"), hypothesis_id=hypothesis_id)

    found = await chain.resolve(session, hypothesis_id=hypothesis_id, language="en")

    assert (found.hypothesis_id, sorted(found.steps)) == (hypothesis_id, [1])
    assert found.steps[1].subject == "Own test question"
    assert found.missing == ["первой добивки", "второй добивки"]
    with pytest.raises(ChainNotReadyError) as refused:
        found.check_ready()
    assert str(refused.value) == (
        f"цепочка писем продаж (en, набор гипотезы №{hypothesis_id}) не задана: "
        "нет первой добивки, второй добивки — задайте на экране «Продажи» → «Цепочка писем»; "
        "набор из файла загружает администратор"
    )


async def test_switched_off_own_steps_leave_the_hypothesis_on_the_common_chain(
    session: AsyncSession,
) -> None:
    hypothesis_id = await _hypothesis(session)
    await _save(session, first(), follow(), follow(3))
    await _save(
        session, first(subject="Draft own question", active=False), hypothesis_id=hypothesis_id
    )

    found = await chain.resolve(session, hypothesis_id=hypothesis_id, language="en")

    assert (found.hypothesis_id, found.version) == (
        None,
        await chain.set_version(session, None, "en"),
    )


async def test_own_chain_is_chosen_per_language(session: AsyncSession) -> None:
    hypothesis_id = await _hypothesis(session)
    await _save(session, first(language="ru"), follow(language="ru"))
    await _save(session, first(), hypothesis_id=hypothesis_id)

    english = await chain.resolve(session, hypothesis_id=hypothesis_id, language="en")
    russian = await chain.resolve(session, hypothesis_id=hypothesis_id, language="ru")

    assert (english.hypothesis_id, russian.hypothesis_id) == (hypothesis_id, None)
    assert sorted(russian.steps) == [1, 2]


async def test_empty_chain_refuses_in_words_there_is_no_default_text(session: AsyncSession) -> None:
    found = await chain.resolve(session, hypothesis_id=None, language="ru")

    assert found.steps == {}
    with pytest.raises(ChainNotReadyError, match="не задана: нет первого письма, первой добивки"):
        found.check_ready()


async def test_switched_off_step_is_not_in_the_chain(session: AsyncSession) -> None:
    await _save(session, first(), follow(), follow(3, active=False))

    found = await chain.resolve(session, hypothesis_id=None, language="en")

    assert (sorted(found.steps), found.missing) == ([1, 2], ["второй добивки"])


async def test_unknown_language_or_hypothesis_is_refused_in_words(session: AsyncSession) -> None:
    with pytest.raises(ChainError, match="языка «de» у цепочки нет"):
        await chain.resolve(session, hypothesis_id=None, language="de")
    with pytest.raises(UnknownHypothesisError, match="гипотезы №987654 нет"):
        await chain.save(session, first(), hypothesis_id=987654, author="т", author_id=None)


# --- запись и журнал ----------------------------------------------------------------------


async def test_new_step_writes_the_author_and_one_journal_line_with_both_versions(
    session: AsyncSession,
) -> None:
    hypothesis_id = await _hypothesis(session)
    empty = await chain.set_version(session, hypothesis_id, "en")

    (row,) = await _save(session, first(), hypothesis_id=hypothesis_id)

    assert (row.hypothesis_id, row.step, row.language, row.updated_by) == (
        hypothesis_id,
        1,
        "en",
        "тест",
    )
    assert (row.subject, row.body, row.active) == (SUBJECT, FIRST_BODY, True)
    assert await _journal(session) == [
        (
            f"sales_chain_template:{row.id}",
            {
                "шаг": 1,
                "язык": "en",
                "набор": f"набор гипотезы №{hypothesis_id}",
                "поля": ["subject", "body", "active"],
                "было": {"subject": None, "body": None, "active": None},
                "версия": {
                    "было": empty,
                    "стало": await chain.set_version(session, hypothesis_id, "en"),
                },
            },
        )
    ]


async def test_change_journals_only_the_changed_fields_with_their_old_values(
    session: AsyncSession,
) -> None:
    (row,) = await _save(session, follow())

    await chain.save(
        session, follow(active=False), hypothesis_id=None, author="другой", author_id=None
    )

    (_, details) = (await _journal(session))[-1]
    assert details is not None
    assert (details["поля"], details["было"], details["набор"]) == (
        ["active"],
        {"active": True},
        "общий набор",
    )
    assert (row.active, row.updated_by) == (False, "другой")


async def test_save_without_a_difference_writes_nothing(session: AsyncSession) -> None:
    (row,) = await _save(session, first())

    same = await chain.save(session, first(), hypothesis_id=None, author="другой", author_id=None)

    assert (same.id, same.updated_by, len(await _journal(session))) == (row.id, "тест", 1)


async def test_save_refuses_the_signature_written_in_the_sender_settings(
    session: AsyncSession,
) -> None:
    await sender.save(session, {"signature": "Iva Testova"}, author="тест", author_id=None)

    with pytest.raises(ChainError, match="подпись из настроек"):
        await _save(session, first(body=FIRST_BODY + "\nIva Testova"))
    assert await chain.rows(session, None) == []


async def test_rows_of_a_set_are_listed_by_step_and_language_switched_off_too(
    session: AsyncSession,
) -> None:
    hypothesis_id = await _hypothesis(session)
    await _save(session, follow(language="ru", active=False), first(), first(language="ru"))
    await _save(session, first(), hypothesis_id=hypothesis_id)

    listed = [(row.step, row.language, row.active) for row in await chain.rows(session, None)]

    assert listed == [(1, "en", True), (1, "ru", True), (2, "ru", False)]
    assert [row.hypothesis_id for row in await chain.rows(session, hypothesis_id)] == [
        hypothesis_id
    ]


# --- предпросмотр -------------------------------------------------------------------------


def test_preview_fills_made_up_values_and_takes_signature_and_address_from_the_settings() -> None:
    settings = _sender(signature="Iva Testova", physical_address="1 Test Street")

    shown = chain_text.preview(first(), settings)

    assert shown.subject == "Test question for Example Company"
    assert [zone.text for zone in shown.zones][:2] == [
        "Hello Alex Example,",
        "This is a made-up test opening about example.com for Example Company only.",
    ]
    assert shown.values == chain_text.SAMPLE["en"]
    assert (shown.sender.values["signature"], shown.sender.missing) == (
        "Iva Testova",
        ["не задано имя отправителя"],
    )
    assert chain_text.preview(follow(language="ru"), settings).subject is None


# --- схема --------------------------------------------------------------------------------


async def test_the_database_keeps_one_common_template_per_step_and_language(
    session: AsyncSession,
) -> None:
    """`NULLS NOT DISTINCT`: без него два общих шаблона одного шага база пустила бы."""
    with pytest.raises(IntegrityError, match="uq_sales_chain_templates_key"):
        async with session.begin_nested():
            for _ in range(2):
                session.add(
                    SalesChainTemplateModel(step=2, language="en", body=FOLLOW_BODY, active=True)
                )
            await session.flush()


@pytest.mark.parametrize(("step", "subject"), [(2, "Test"), (1, None)])
async def test_the_database_keeps_the_subject_only_on_the_first_letter(
    session: AsyncSession, step: int, subject: str | None
) -> None:
    row = SalesChainTemplateModel(
        step=step, language="en", subject=subject, body=FOLLOW_BODY, active=True
    )

    with pytest.raises(IntegrityError, match="ck_sales_chain_templates_subject"):
        async with session.begin_nested():
            session.add(row)
            await session.flush()


def _migration(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"sales_chain_migration_{path.stem}", path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table(connection: Connection) -> list[str]:
    found = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = 'sales_chain_templates'")
    )
    return list(found.scalars())


def _down_and_up(connection: Connection) -> tuple[list[str], list[str]]:
    migration = _migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        after_downgrade = _table(connection)
        migration.upgrade()
    return after_downgrade, _table(connection)


async def test_migration_creates_the_table_and_takes_it_back(session: AsyncSession) -> None:
    """Цикл — на соединении теста: DDL в Postgres транзакционный, откат теста вернёт схему."""
    connection = await session.connection()

    after_downgrade, after_upgrade = await connection.run_sync(_down_and_up)

    assert (after_downgrade, after_upgrade) == ([], ["sales_chain_templates"])


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

    assert (await connection.run_sync(_journal_values)).count("sales_chain_changed") == 1
