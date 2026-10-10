"""Сборка очереди писем продаж на настоящей базе — срез 4.6b, T3.

Мир — подключённые продажи (`tests/test_sales_send_world.py`): своя учётка, «Отправитель»,
цепочка общим набором на двух языках, ящики обоих этапов. Модель — подделка: переписывает
зоны `rewrite` ровно до коридора отличия (или не переписывает вовсе). Тексты выдуманные.
Что ушло в базу — проверяется в базе: диалог, связь с лидом, письмо, ключ.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from backend.cli.main import main
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.core.domain import ContactSource, MessageStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters.uniqueness import in_corridor
from backend.features.sales import chain, chain_text, hypotheses, queue
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHandoffModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import TEST_DSN

JANE = "jane@acme.example.test"
OLGA = "olga@acme.example.test"
IVAN = "ivan@beta.example.test"
#: Блок «Отправителя» в конце письма — так его допишет сборка.
SIGNED = f"\n\n{w.SIGNATURE}\n\n{w.ADDRESS}"


async def _build(
    session: AsyncSession, world: w.World, rewriter: Any = None, limit: int = 10
) -> queue.QueueReport:
    return await queue.build(
        session, rewriter or w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=limit
    )


async def _letters(session: AsyncSession) -> list[MessageModel]:
    return list(await session.scalars(select(MessageModel).order_by(MessageModel.id)))


async def _link(session: AsyncSession, message: MessageModel) -> SalesThreadModel:
    found = await session.get(SalesThreadModel, message.thread_id)
    assert found is not None
    return found


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


# --- A1: письмо лида EN -----------------------------------------------------------------------


async def test_a1_en_lead_gets_an_en_first_letter_signed_by_the_settings(
    session: AsyncSession, world: w.World
) -> None:  # A1
    jane = await w.lead(session, world.hypothesis_id, JANE)
    rewriter = w.CorridorRewriter()

    report = await _build(session, world, rewriter)

    assert (report.prepared, report.refreshed, report.off_corridor) == (1, 0, 0)
    assert report.tokens_spent == 37
    [letter] = await _letters(session)
    assert letter.status is MessageStatus.QUEUED
    assert letter.step == 0
    assert letter.subject == "A made-up question for Example Test Co"
    assert not letter.subject.lower().startswith(("re:", "fwd:", "fw:"))
    assert letter.body is not None
    assert letter.body.endswith(f"Made-up test offer: nothing real is sold here.{SIGNED}")
    assert letter.body.count(w.SIGNATURE) == 1
    assert letter.uniqueness_pct is not None
    assert in_corridor(letter.uniqueness_pct)
    assert letter.idempotency_key == f"sales:acme.example.test:{JANE}:0"
    assert letter.contact_id is None
    assert letter.domain_id == jane.domain_id
    assert [about.host for about in rewriter.seen] == ["acme.example.test"]


async def test_a1_link_names_the_lead_the_chain_set_language_and_version(
    session: AsyncSession, world: w.World
) -> None:  # A1
    jane = await w.lead(session, world.hypothesis_id, JANE)

    await _build(session, world)

    [letter] = await _letters(session)
    link = await _link(session, letter)
    common = await chain.resolve(session, hypothesis_id=world.hypothesis_id, language="en")
    assert (link.lead_id, link.chain_hypothesis_id, link.language, link.chain_version) == (
        jane.id,
        None,
        "en",
        common.version,
    )
    thread = await session.get(ThreadModel, letter.thread_id)
    campaign = await session.get(CampaignModel, letter.campaign_id)
    assert thread is not None
    assert campaign is not None
    assert thread.contact_id is None
    assert (campaign.stage, campaign.name, campaign.followup_days) == (
        Stage.SALES,
        "Продажи: Выдуманная гипотеза",
        [3, 5],
    )


async def test_ru_lead_gets_the_ru_chain(session: AsyncSession, world: w.World) -> None:
    await w.lead(
        session, world.hypothesis_id, IVAN, name="Иван Пример", company="Бета", language="ru"
    )

    await _build(session, world)

    [letter] = await _letters(session)
    assert letter.subject == "Выдуманный вопрос для Бета"
    assert letter.body is not None
    assert "Выдуманное тестовое предложение" in letter.body
    assert (await _link(session, letter)).language == "ru"


async def test_address_of_the_letter_is_the_lead_not_a_contact_of_the_domain(
    session: AsyncSession, world: w.World
) -> None:
    """Решение 01.10: адрес лида — у лида; строка `contacts` домена — адрес сайта для доноров.
    Мутант «адрес из contacts вместо лида» сделал бы письмо этому адресу."""
    jane = await w.lead(session, world.hypothesis_id, JANE)
    session.add(
        ContactModel(
            domain_id=jane.domain_id, email="info@acme.example.test", source=ContactSource.PAGE
        )
    )
    await session.flush()

    await _build(session, world)

    [letter] = await _letters(session)
    assert letter.contact_id is None
    assert (await _link(session, letter)).lead_id == jane.id


# --- A2: без физического адреса письмо не собирается -------------------------------------------


async def test_a2_without_a_physical_address_nothing_is_built(
    session: AsyncSession, world: w.World
) -> None:  # A2
    await w.lead(session, world.hypothesis_id, JANE)
    await w.settings(session, physical_address=None)

    with pytest.raises(SalesNotConnectedError) as refused:
        await _build(session, world, w.NoRewrite())

    assert str(refused.value).startswith(
        f"{queue.WHAT}: {SALES_NOT_CONNECTED} — не задан физический адрес"
    )
    campaigns = await session.scalar(
        select(func.count()).select_from(CampaignModel).where(CampaignModel.stage == Stage.SALES)
    )
    assert (campaigns, await _letters(session)) == (0, [])


# --- A3: два лида одной компании -------------------------------------------------------------


async def test_a3_two_leads_of_one_company_get_two_letters_with_their_own_keys(
    session: AsyncSession, world: w.World
) -> None:  # A3
    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga Example")

    report = await _build(session, world)

    letters = await _letters(session)
    assert report.prepared == 2
    assert [letter.idempotency_key for letter in letters] == [
        f"sales:acme.example.test:{JANE}:0",
        f"sales:acme.example.test:{OLGA}:0",
    ]
    assert len({letter.thread_id for letter in letters}) == 2
    assert len({letter.domain_id for letter in letters}) == 1


# --- A4: письмо вне коридора не уходит -------------------------------------------------------


@pytest.mark.parametrize("rewriter", [w.FlatRewriter(), w.CorridorRewriter(target=0.6)])
async def test_a4_letter_outside_the_corridor_is_not_queued(
    session: AsyncSession, world: w.World, rewriter: Any
) -> None:  # A4
    """Ниже коридора — модель не переписала (нет ключа, отказ); выше — переписала лишнее.
    Очередь продаж уходит пачкой, без глаз на каждом письме: лид ждёт следующей сборки."""
    await w.lead(session, world.hypothesis_id, JANE)

    report = await _build(session, world, rewriter)

    assert (report.prepared, report.off_corridor) == (0, 1)
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})
    assert await _letters(session) == []
    assert await session.scalar(select(func.count()).select_from(SalesThreadModel)) == 0


# --- кому пока не пишем ----------------------------------------------------------------------


async def test_leads_that_cannot_be_written_cost_no_model_call(
    session: AsyncSession, world: w.World
) -> None:
    """Передан, в стоп-листе, нет имени для {{name}}, язык без цепочки — причины в отчёте,
    модель не звали ни разу."""
    handed = await w.lead(session, world.hypothesis_id, "handed@one.example.test")
    await w.lead(session, world.hypothesis_id, "stop@two.example.test")
    await w.lead(session, world.hypothesis_id, "noname@three.example.test", name=None)
    await w.lead(session, world.hypothesis_id, "de@four.example.test", language="de")
    await w.lead(session, world.hypothesis_id, "none@five.example.test", language=None)
    thread = ThreadModel(domain_id=handed.domain_id, campaign_id=await _campaign(session))
    session.add(thread)
    await session.flush()
    session.add_all(
        [
            SalesHandoffModel(thread_id=thread.id, lead_id=handed.id),
            SalesStoplistModel(email="stop@two.example.test"),
        ]
    )
    await session.flush()

    report = await _build(session, world, w.NoRewrite())

    assert report.prepared == 0
    assert report.waiting == Counter(
        {
            queue.HANDED_OFF: 1,
            queue.STOPLIST: 1,
            "у лида нет значения для {{name}}": 1,
            queue.NO_LANGUAGE: 2,
        }
    )


async def _campaign(session: AsyncSession) -> int:
    campaign = CampaignModel(stage=Stage.SALES, name="Прежняя рассылка", status="running")
    session.add(campaign)
    await session.flush()
    return campaign.id


async def test_lead_of_an_incomplete_language_waits_others_are_written(
    session: AsyncSession, world: w.World
) -> None:
    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, IVAN, name="Иван", company="Бета", language="ru")
    last = chain_text.step_template(step=3, language="ru", body=w.RU_FOLLOW[3], active=False)
    await chain.save(session, last, hypothesis_id=None, author="тест", author_id=None)

    report = await _build(session, world)

    assert report.prepared == 1
    assert report.waiting == Counter({"цепочка на языке ru задана не целиком": 1})
    [letter] = await _letters(session)
    assert letter.idempotency_key.endswith(f"{JANE}:0")


async def test_rejected_and_new_leads_are_not_written(
    session: AsyncSession, world: w.World
) -> None:
    await w.lead(session, world.hypothesis_id, JANE, status=LeadStatus.NEW)
    await w.lead(session, world.hypothesis_id, OLGA, status=LeadStatus.REJECTED)

    report = await _build(session, world, w.NoRewrite())

    assert (report.prepared, sum(report.waiting.values())) == (0, 0)


async def test_referral_lead_gets_the_same_first_step_for_now(
    session: AsyncSession, world: w.World
) -> None:
    """Письма «вас посоветовал коллега» пока нет — открытый вопрос владельцу."""
    await w.lead(session, world.hypothesis_id, JANE, source=LeadSource.REFERRAL)

    await _build(session, world)

    [letter] = await _letters(session)
    assert letter.subject == "A made-up question for Example Test Co"


async def test_own_chain_of_the_hypothesis_is_written_down_in_the_link(
    session: AsyncSession, world: w.World
) -> None:
    await w.chain_of(session, hypothesis_id=world.hypothesis_id)
    await w.lead(session, world.hypothesis_id, JANE)

    await _build(session, world)

    [letter] = await _letters(session)
    assert (await _link(session, letter)).chain_hypothesis_id == world.hypothesis_id


# --- повтор сборки -----------------------------------------------------------------------------


async def test_repeat_does_not_double_and_does_not_call_the_model(
    session: AsyncSession, world: w.World
) -> None:
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)

    report = await _build(session, world, w.NoRewrite())

    assert (report.prepared, report.refreshed) == (0, 0)
    assert report.waiting == Counter({queue.UP_TO_DATE: 1})
    assert len(await _letters(session)) == 1


async def test_queued_letter_with_old_signature_is_built_again_in_place(
    session: AsyncSession, world: w.World
) -> None:
    """Подпись сменилась после сборки: сверка на отправке такое письмо не пустит, а второе
    письмо лиду не даст ключ. Повторная сборка собирает его заново на том же месте."""
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)
    [before] = await _letters(session)
    await w.settings(session, signature="Mira Testova\nAnother Made-up Agency")

    report = await _build(session, world)

    assert (report.prepared, report.refreshed) == (0, 1)
    [after] = await _letters(session)
    assert after.id == before.id
    assert after.body is not None
    assert after.body.endswith(f"\n\nMira Testova\nAnother Made-up Agency\n\n{w.ADDRESS}")


async def test_queued_letter_of_an_old_chain_version_is_built_again(
    session: AsyncSession, world: w.World
) -> None:
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)
    edited = chain_text.step_template(
        step=1, language="en", subject="Another made-up question", body=w.FIRST_BODY
    )
    await chain.save(session, edited, hypothesis_id=None, author="тест", author_id=None)

    report = await _build(session, world)

    assert report.refreshed == 1
    [letter] = await _letters(session)
    assert letter.subject == "Another made-up question"
    common = await chain.resolve(session, hypothesis_id=world.hypothesis_id, language="en")
    assert (await _link(session, letter)).chain_version == common.version


class _SentMeanwhile(w.CorridorRewriter):
    """Модель переписывает письмо, а пачка тем временем его отправила — другой задачей, мимо
    этой сессии: строка в базе уже не в очереди, а прочитанная сборкой — ещё в очереди."""

    def __init__(self, session: AsyncSession, message_id: int) -> None:
        super().__init__()
        self._session, self._message_id = session, message_id

    async def rewrite(self, rendered: Any, about: Any) -> Any:
        await self._session.execute(
            text("UPDATE messages SET status = 'sent' WHERE id = :id"), {"id": self._message_id}
        )
        return await super().rewrite(rendered, about)


async def test_letter_sent_while_the_queue_was_rebuilt_stays_as_it_was_sent(
    session: AsyncSession, world: w.World
) -> None:
    """Сборка и пачка — разные задачи. Пока сборка переписывала письмо новой цепочкой, пачка
    его отправила: в базе остаётся то, что ушло, — тема, текст и версия цепочки первого письма,
    по которым идут добивки; сборка называет это в отчёте."""
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)
    [letter] = await _letters(session)
    sent = (letter.subject, letter.body, (await _link(session, letter)).chain_version)
    edited = chain_text.step_template(
        step=1, language="en", subject="Another made-up question", body=w.FIRST_BODY
    )
    await chain.save(session, edited, hypothesis_id=None, author="тест", author_id=None)

    report = await _build(session, world, _SentMeanwhile(session, letter.id))

    assert (report.prepared, report.refreshed) == (0, 0)
    assert report.waiting == Counter({queue.SENT_MEANWHILE: 1})
    stored = await session.execute(
        text(
            "SELECT m.subject, m.body, t.chain_version FROM messages m "
            "JOIN sales_threads t ON t.thread_id = m.thread_id WHERE m.id = :id"
        ),
        {"id": letter.id},
    )
    assert tuple(stored.one()) == sent


async def test_template_written_before_the_signature_does_not_double_it(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заметка 4.6a: правило записи сверяет шаблон с подписью при записи; шаблон, записанный
    до подписи, мог её содержать. Готовое письмо сверяется ещё раз — и в очередь не встаёт."""
    w.connect(monkeypatch)
    hypothesis = await hypotheses.add(session, "Подпись после шаблона", None)
    await w.settings(session, signature=None)
    doubled = w.FIRST_BODY.replace(
        "[offer] fixed\n", "[offer] fixed\nMira Testova Made-up Test Agency.\n"
    )
    await chain.save(
        session,
        chain_text.step_template(step=1, language="en", subject=w.SUBJECT, body=doubled),
        hypothesis_id=None,
        author="тест",
        author_id=None,
    )
    for step in (2, 3):
        follow = chain_text.step_template(step=step, language="en", body=w.FOLLOW_BODY[step])
        await chain.save(session, follow, hypothesis_id=None, author="тест", author_id=None)
    await w.settings(session)
    await w.lead(session, hypothesis.id, JANE)

    report = await queue.build(session, w.CorridorRewriter(), hypothesis_id=hypothesis.id, limit=5)

    assert report.prepared == 0
    [(problem, count)] = report.waiting.items()
    assert count == 1
    assert problem.startswith("подпись из настроек отправителя стоит и в тексте письма")


async def test_limit_counts_letters_not_leads_looked_at(
    session: AsyncSession, world: w.World
) -> None:
    await w.lead(session, world.hypothesis_id, "de@one.example.test", language="de")
    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga Example")

    report = await _build(session, world, limit=1)

    assert (report.prepared, report.waiting) == (1, Counter({queue.NO_LANGUAGE: 1}))


async def test_spend_cap_of_the_model_stops_the_build_before_the_next_letter(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок расхода на модель — до каждого письма, как у доноров: собранное остаётся,
    следующее письмо модели не стоит, отчёт называет остановку словами потолка."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 53)  # выдуманный потолок, 37 токенов на письмо
    # Свой потолок продаж (доля общего) — свой тест в `test_sales_usage_cap.py`; здесь — общий.
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 0)
    for name in ("one", "two", "three"):
        await w.lead(session, world.hypothesis_id, f"{name}@{name}.example.test")
    rewriter = w.CorridorRewriter()

    report = await _build(session, world, rewriter)

    assert (report.prepared, report.tokens_spent, len(rewriter.seen)) == (2, 74, 2)
    assert report.stopped is not None
    assert report.stopped.startswith("потолок расхода на модель за день достигнут: 74 из 53")
    assert len(await _letters(session)) == 2


async def test_unknown_hypothesis_is_refused_in_words(
    session: AsyncSession, world: w.World
) -> None:
    with pytest.raises(UnknownHypothesisError, match="гипотезы №987 нет"):
        await queue.build(session, w.NoRewrite(), hypothesis_id=987, limit=5)  # type: ignore[arg-type]


# --- консоль ---------------------------------------------------------------------------------


async def test_console_prints_the_report(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.cli.sales_queue import run_sales_queue  # noqa: PLC0415

    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, "de@one.example.test", language="de")

    code = await run_sales_queue(session, w.CorridorRewriter(), "Выдуманная гипотеза", 5)  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert code == 0
    assert "новых писем: 1, собрано заново: 0" in out
    assert f"ждут — {queue.NO_LANGUAGE}: 1" in out
    assert "Ничего не отправлено" in out


async def test_console_names_an_unknown_hypothesis(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.cli.sales_queue import EXIT_NO_HYPOTHESIS, run_sales_queue  # noqa: PLC0415

    code = await run_sales_queue(session, w.NoRewrite(), "Нет такой", 5)  # type: ignore[arg-type]

    assert code == EXIT_NO_HYPOTHESIS
    assert "Гипотезы «Нет такой» нет" in capsys.readouterr().out


async def test_console_lets_the_refusal_of_connection_through_to_main(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ подключения идёт в `main` как есть: там он — «Отказ: …» и код 9 (1.1b)."""
    from backend.cli.sales_queue import run_sales_queue  # noqa: PLC0415

    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    with pytest.raises(SalesNotConnectedError, match="модуль продаж выключен — включает"):
        await run_sales_queue(session, w.NoRewrite(), "Выдуманная гипотеза", 5)  # type: ignore[arg-type]


def test_console_command_is_wired_into_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Через `main` в чистой базе: команда знает себя, неизвестная гипотеза — код 5."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(storage, "REDIS_URL", "redis://localhost:1/0")
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    code = main(["sales-queue", "--hypothesis", "Нет такой гипотезы", "--limit", "3"])

    assert code == 5
    assert "Гипотезы «Нет такой гипотезы» нет" in capsys.readouterr().out
