"""Ответ лиду продаж из переписки — мостом почты к модулю продаж.

Ответ — общий путь (`letters/answers.answer_reply`): ветка к письму лида, ящик переписки,
общая отправка. О лиде почта спрашивает мост до заведения письма (`stages.answer_text`):

- продажи подключены — ответ уходит лиду с ящика продаж, текст — с подписью и физическим
  адресом из «Отправителя» в конце (`sales/letter.answered`); полная цепочка ответу не нужна
  (`sales/mail.recipient`);
- не подключены — отказ словами с тем, чего не хватает; лиду писать нельзя — отказ словами;
  письма ответа в базе нет ни в том, ни в другом случае;
- метрики Ahrefs проверяются в итоговом тексте — с подписью и адресом из настроек, как у
  сборки очереди: подпись с метрикой — отказ словами, письма нет;
- перед отправкой модуль проверяет ответ ещё раз (`sales/mail.check`): блок настроек в конце
  и нынешний; подпись в тексте ответа — не отказ, в отличие от письма цепочки;
- у доноров и рекламодателей путь прежний: текст как написан, модуль продаж не спрошен.

Мир — подключённые продажи (`tests/test_sales_send_world.py`): первое письмо ушло лиду общей
отправкой, лид ответил. Транспорт подставной, адреса `*.example.test`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.features.agent.drafts import Decider, send_draft
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core import stages
from backend.features.core.domain import DraftStatus, MessageStatus, ReplyKind, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters.answers import answer_key, answer_reply
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.sending import NotReadyError, Sending, SendOutcome
from backend.features.sales import chain
from backend.features.sales.agent import parts
from backend.features.sales.mail import LeadStoppedError
from backend.features.sales.models import (
    SalesChainTemplateModel,
    SalesHandoffModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_replies_inbox import sent
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_send import JANE, SIGNED, _first_sent, _seen
from tests.test_sales_stage_bridge import FakeSalesMail, _transports, fake, unregistered
from tests.test_sales_stage_mail import NOW, sales_world
from tests.test_thread_answer import Recording, conversation

__all__ = ["conversation", "fake", "sales_on", "sent", "unregistered"]  # фикстуры — их видит pytest

TEXT = "Thanks for the question. The audit covers the pages that bring you search traffic."


@dataclass(frozen=True, slots=True)
class Answered:
    """Лид ответил на первое письмо продаж; транспорты помнят, что ушло."""

    first: MessageModel
    reply: ReplyModel
    source: _ByStage

    async def answer(self, session: AsyncSession, body: str = TEXT) -> SendOutcome:
        return await answer_reply(
            session,
            Sending(session, self.source, now=w.NOW),
            thread_id=self.first.thread_id or 0,
            reply_id=self.reply.id,
            body=body,
            author_id=None,
        )


@pytest.fixture
async def answered(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Answered:
    world = await w.world(session, monkeypatch)
    source, [first] = await _first_sent(session, world)
    reply = ReplyModel(
        thread_id=first.thread_id,
        message_id=first.id,
        kind=ReplyKind.HUMAN,
        raw_body="What does the audit include?",
        from_email=JANE,
        subject=f"Re: {first.subject}",
        inbound_message_id="<in-1@acme.example.test>",
        created_at=w.NOW + timedelta(hours=1),  # после первого письма: черновик не устарел
    )
    session.add(reply)
    await session.flush()
    return Answered(first, reply, source)


async def _answers(session: AsyncSession, reply_id: int) -> int:
    """Сколько писем-ответов на ответ лида в базе — с любым состоянием."""
    found = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.answers_reply_id == reply_id)
    )
    return int(found or 0)


# --- продажи подключены — ответ уходит --------------------------------------------------------


async def test_the_answer_goes_to_the_lead_in_the_thread_from_the_sales_box_signed(
    session: AsyncSession, answered: Answered
) -> None:
    outcome = await answered.answer(session)

    [_, out] = _seen(answered.source)
    assert (out.to, out.from_email, out.from_name) == (JANE, w.SALES_BOX, w.SENDER_NAME)
    assert out.in_reply_to == answered.reply.inbound_message_id  # ветка к письму лида
    assert out.subject == answered.reply.subject
    assert out.body == f"{TEXT}{SIGNED}"  # подпись и физический адрес из «Отправителя»
    stored = await session.get(MessageModel, outcome.message_id)
    assert stored is not None
    assert (stored.step, stored.status, stored.answers_reply_id, stored.body) == (
        ANSWER_STEP,
        MessageStatus.SENT,
        answered.reply.id,
        out.body,
    )


async def test_a_signature_in_the_answer_text_is_not_a_refusal(
    session: AsyncSession, answered: Answered
) -> None:
    """Ответ пишет человек или агент, и прощание с подписью — обычное дело: правило письма
    цепочки («подпись из настроек повторена в тексте — шаблон её повторяет») ответу не годится."""
    body = f"Thanks for the question.\n\nBest regards,\n{w.SIGNATURE}"

    await answered.answer(session, body)

    assert _seen(answered.source)[-1].body == f"{body}{SIGNED}"


async def test_an_answer_with_the_settings_block_already_at_its_end_is_not_signed_twice(
    session: AsyncSession, answered: Answered
) -> None:
    await answered.answer(session, f"{TEXT}{SIGNED}")

    assert _seen(answered.source)[-1].body == f"{TEXT}{SIGNED}"


async def test_the_answer_does_not_need_a_full_chain(
    session: AsyncSession, answered: Answered
) -> None:
    """Ответ — не шаг цепочки: последний шаг выключили после первого письма — добивке
    цепочки не хватает, а ответ лиду уходит."""
    await session.execute(
        update(SalesChainTemplateModel)
        .where(SalesChainTemplateModel.step == 3)
        .values(active=False)
    )
    link = await session.get(SalesThreadModel, answered.first.thread_id)
    assert link is not None
    assert (await chain.of_set(session, link.chain_hypothesis_id, link.language)).missing

    await answered.answer(session)

    assert [out.to for out in _seen(answered.source)] == [JANE, JANE]


@pytest.mark.usefixtures("sales_on")
async def test_send_as_is_of_a_sales_draft_answers_the_lead_and_the_draft_is_sent_as_is(
    session: AsyncSession, answered: Answered
) -> None:
    """«Подходит · отправить»: черновик агента продаж уходит ответом лиду в том же треде — с
    подписью и адресом из настроек, а черновик закрыт «как есть»: блок, дописанный при
    отправке, — не правка человека (калибровка считает правки по тексту черновика)."""
    repository = AgentSettingsRepository(session)
    await repository.save(Stage.SALES, parts.DEFAULTS, author="тест")
    settings = await repository.current(Stage.SALES)
    assert settings is not None
    draft = AgentDraftModel(
        reply_id=answered.reply.id,
        settings_id=settings.id,
        status=DraftStatus.DRAFTED,
        body=TEXT,
        model="made-up-model",
        prompt_version="made-up-prompt-v1",
    )
    session.add(draft)
    await session.flush()

    sent_out = await send_draft(
        session,
        Sending(session, answered.source, now=w.NOW),
        draft.id,
        body=None,
        by=Decider(name="desk@sales.example.test"),
    )

    out = _seen(answered.source)[-1]
    assert (out.to, out.in_reply_to, out.body) == (
        JANE,
        answered.reply.inbound_message_id,
        f"{TEXT}{SIGNED}",
    )
    await session.refresh(draft)
    assert (draft.status, draft.edited, draft.final_body, draft.sent_message_id) == (
        DraftStatus.SENT,
        False,
        TEXT,
        sent_out.message_id,
    )


# --- нельзя — отказ словами до заведения письма ------------------------------------------------


async def test_not_connected_sales_refuse_the_answer_in_words_and_store_nothing(
    session: AsyncSession, answered: Answered, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ называет, чего не хватает, — тем же правилом, что у отправки письма продаж
    (`sales/connection.py`), — и приходит до заведения письма: ответ в очереди не остаётся."""
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    with pytest.raises(SalesNotConnectedError) as refused:
        await answered.answer(session)

    assert str(refused.value) == (
        f"Ответ в переписке №{answered.first.thread_id}: {SALES_NOT_CONNECTED} — "
        "продажи выключены: SALES_ENABLED не включён"
    )
    assert await _answers(session, answered.reply.id) == 0
    assert len(_seen(answered.source)) == 1  # только первое письмо


@pytest.mark.parametrize(
    ("how", "words"),
    [
        ("handed-off", f"лид {JANE} у телемаркетолога — письма продаж ему не идут"),
        ("stop-list", f"лиду {JANE} писать нельзя: в ручном стоп-листе продаж"),
    ],
    ids=["handed-off", "stop-list"],
)
async def test_a_lead_that_must_not_be_written_gets_no_answer_and_nothing_is_stored(
    session: AsyncSession, answered: Answered, how: str, words: str
) -> None:
    link = await session.get(SalesThreadModel, answered.first.thread_id)
    assert link is not None
    if how == "handed-off":
        session.add(SalesHandoffModel(thread_id=link.thread_id, lead_id=link.lead_id))
    else:
        session.add(SalesStoplistModel(email=JANE))
    await session.flush()

    with pytest.raises(LeadStoppedError, match=words):
        await answered.answer(session)

    assert await _answers(session, answered.reply.id) == 0


async def test_a_broken_sales_module_refuses_the_answer_in_words_and_stores_nothing(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    """Поломка модуля — не его отказ словами: мост называет её «не подключены» с причиной."""
    world = await sales_world(session, status=MessageStatus.SENT)
    fake.broken = "answer"

    with pytest.raises(SalesNotConnectedError) as refused:
        await answer_reply(
            session,
            Sending(session, _transports(), now=NOW),
            thread_id=world.thread.id,
            reply_id=world.reply.id,
            body="Thanks.",
            author_id=None,
        )

    assert str(refused.value) == (
        f"Ответ в переписке №{world.thread.id}: {SALES_NOT_CONNECTED} — "
        "выдуманная поломка модуля: answer"
    )
    assert await _answers(session, world.reply.id) == 0


async def test_metrics_in_the_signature_stop_the_answer_in_words_and_store_nothing(
    session: AsyncSession, answered: Answered
) -> None:
    """Текст ответа почта проверила до подписи; подпись и адрес из настроек — тоже текст письма:
    метрики Ahrefs ищутся в итоговом тексте, как у сборки очереди — в собранном письме."""
    await w.settings(session, signature="Mira Testova\nMade-up Test Agency, an Ahrefs partner")

    with pytest.raises(NotReadyError, match="метрики Ahrefs") as refused:
        await answered.answer(session)

    assert str(refused.value).startswith(f"Ответ в переписке №{answered.first.thread_id} не уходит")
    assert "уберите их из текста ответа или из подписи и адреса" in str(refused.value)
    assert await _answers(session, answered.reply.id) == 0
    assert len(_seen(answered.source)) == 1  # только первое письмо


# --- перед отправкой — проверка ещё раз ---------------------------------------------------------


async def test_an_answer_without_the_settings_block_at_its_end_does_not_go(
    session: AsyncSession, answered: Answered
) -> None:
    """Ответ, записанный мимо моста или до смены настроек отправителя, ждёт: блок настроек
    — подпись и адрес — в конце ответа обязателен, как у письма цепочки."""
    first = answered.first
    letter = MessageModel(
        campaign_id=first.campaign_id,
        thread_id=first.thread_id,
        domain_id=first.domain_id,
        step=ANSWER_STEP,
        status=MessageStatus.QUEUED,
        subject=answered.reply.subject,
        body=TEXT,
        idempotency_key=answer_key(Stage.SALES, "acme.example.test", answered.reply.id),
        answers_reply_id=answered.reply.id,
    )
    session.add(letter)
    await session.flush()

    # Слова ответа, а не письма цепочки («соберите очередь заново»): ответ отправляют заново.
    with pytest.raises(NotReadyError, match="в конце ответа нет — настройки сменились"):
        await Sending(session, answered.source, now=w.NOW).send(
            letter.id, from_sender_id=first.sender_id
        )

    assert len(_seen(answered.source)) == 1


# --- доноры и рекламодатели — путь прежний ----------------------------------------------------


async def test_a_donor_answer_goes_as_typed_and_the_sales_module_is_not_asked(
    session: AsyncSession, conversation: tuple[MessageModel, ReplyModel], fake: FakeSalesMail
) -> None:
    first, reply = conversation
    transport = Recording()
    body = "Thanks! A made-up guide on home repair suits us."

    outcome = await answer_reply(
        session,
        Sending(session, transport),
        thread_id=first.thread_id or 0,
        reply_id=reply.id,
        body=body,
        author_id=None,
    )

    [out] = transport.seen
    stored = await session.get(MessageModel, outcome.message_id)
    assert stored is not None
    assert (out.body, stored.body) == (body, body)
    assert fake.asked == []


@pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS])
async def test_donors_and_advertisers_answer_text_is_as_written(
    session: AsyncSession, fake: FakeSalesMail, stage: Stage
) -> None:
    assert await stages.answer_text(session, stage, 1, "As written.", "Ответ") == "As written."
    assert fake.asked == []
