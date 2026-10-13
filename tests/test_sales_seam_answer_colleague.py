"""Решение владельца 13.10: коллега лида ответил со своего адреса на домене компании — ответ уходит
тому, кто написал (ревью стыков, круг 2: до решения — `xfail` в `test_sales_seam_handoff_people.py`).

Первое письмо спрашивает, кто у компании отвечает за маркетинг, и ответить может коллега лида со
своего адреса. Агент пишет «Здравствуйте, <коллега>», а письмо уходило на адрес лида
(`sales/mail.recipient`: «адрес — лида»). Теперь:

- ответ в переписке — ручной и черновик агента, оба уходят общей отправкой, — получает тот, кто
  написал, если его адрес на домене компании (`mail.colleague_of`: сайт компании или домен адреса
  лида, их поддомены);
- чужой домен, бесплатная почта (даже если на ней сам лид), робот, сам лид в другом регистре —
  адрес лида, как прежде; цепочка — первое письмо и добивки — пишет лиду всегда;
- стоп-лист судит и адрес коллеги: ручной стоп-лист продаж и общий по правилу продаж
  (`holds_sales`: «сам просил не писать» — на любом этапе); отказ словами, письмо не уходит;
- события доставки ответа коллеге — о нашем письме: переписки адресата (`mail.threads_to`)
  называют и ту, где ответ ушёл коллеге. Иначе отказ и жалоба на этот ответ были бы «событием о
  чужом письме», и их последствия пропали бы.

Мир — подключённые продажи (`tests/test_sales_send_world.py`): первое письмо ушло лиду общей
отправкой, лид или коллега ответил. Транспорт подставной, адреса `*.example.test`.
"""

from __future__ import annotations

import re

import pytest
from backend.features.agent.drafts import Decider, send_draft
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import DraftStatus, MessageStatus, Stage, SuppressionReason
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import stoplist
from backend.features.letters.answers import answer_key
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.sending import Sending
from backend.features.replies.repository import ReplyRepository
from backend.features.sales import mail
from backend.features.sales.agent import parts
from backend.features.sales.mail import LeadStoppedError
from backend.features.sales.models import SalesLeadModel, SalesStoplistModel, SalesThreadModel
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_send import JANE, _seen
from tests.test_sales_thread_answer import TEXT, Answered, answered

__all__ = ["answered", "sales_on"]  # фикстуры — отсюда их видит pytest

COLLEAGUE = "olga@acme.example.test"


async def _from(session: AsyncSession, answered: Answered, sender: str | None) -> None:
    """Ответ лида пришёл с этого адреса."""
    answered.reply.from_email = sender
    await session.flush()


async def _draft(session: AsyncSession, answered: Answered) -> AgentDraftModel:
    """Черновик агента продаж на ответ — как его оставляет задача черновика."""
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
    return draft


# --- ответ коллеге с домена компании ------------------------------------------------------------


async def test_the_answer_goes_to_the_colleague_who_wrote(
    session: AsyncSession, answered: Answered
) -> None:
    """Ручной ответ на письмо коллеги — коллеге: той же веткой и с ящика переписки."""
    await _from(session, answered, COLLEAGUE)

    outcome = await answered.answer(session)

    [first, out] = _seen(answered.source)
    assert first.to == JANE, "первое письмо — лиду"
    assert (out.to, out.from_email, out.in_reply_to) == (
        COLLEAGUE,
        w.SALES_BOX,
        answered.reply.inbound_message_id,
    )
    stored = await session.get(MessageModel, outcome.message_id)
    assert stored is not None
    assert (stored.status, stored.answers_reply_id) == (MessageStatus.SENT, answered.reply.id)


@pytest.mark.usefixtures("sales_on")
async def test_a_sales_draft_sent_as_is_goes_to_the_colleague_who_wrote(
    session: AsyncSession, answered: Answered
) -> None:
    """«Подходит · отправить» — черновик агента уходит той же отправкой, что ручной ответ:
    коллеге, которому агент и писал «Здравствуйте, <коллега>»."""
    await _from(session, answered, COLLEAGUE)
    draft = await _draft(session, answered)

    await send_draft(
        session,
        Sending(session, answered.source, now=w.NOW),
        draft.id,
        body=None,
        by=Decider(name="desk@sales.example.test"),
    )

    assert _seen(answered.source)[-1].to == COLLEAGUE
    await session.refresh(draft)
    assert draft.status is DraftStatus.SENT


@pytest.mark.parametrize(
    "sender",
    [
        "olga@beta.example.test",
        "olga@acme.example.test.elsewhere.test",
        "olga.acme@gmail.com",
        "noreply@acme.example.test",
        JANE.upper(),
        None,
    ],
    ids=["other-domain", "lookalike-domain", "free-mail", "robot", "lead-in-caps", "no-address"],
)
async def test_an_answer_to_anyone_else_goes_to_the_lead_as_before(
    session: AsyncSession, answered: Answered, sender: str | None
) -> None:
    """Граница правила: адрес не на домене компании, бесплатная почта, робот, сам лид, адреса
    нет — ответ уходит лиду, как до решения."""
    await _from(session, answered, sender)

    await answered.answer(session)

    assert [out.to for out in _seen(answered.source)] == [JANE, JANE]


def _dialog(lead: str, host: str = "acme.example.test") -> mail.Linked:
    return mail.Linked(SalesThreadModel(thread_id=1, lead_id=1), SalesLeadModel(email=lead), host)


@pytest.mark.parametrize(
    ("lead", "host", "sender", "to"),
    [
        (JANE, "acme.example.test", COLLEAGUE, COLLEAGUE),
        (JANE, "acme.example.test", "  Olga@ACME.example.test ", COLLEAGUE),
        (JANE, "acme.example.test", "olga@mail.acme.example.test", "olga@mail.acme.example.test"),
        (
            "jane@acme-group.example.test",
            "acme.example.test",
            "olga@acme-group.example.test",
            "olga@acme-group.example.test",
        ),
        ("jane.example@gmail.com", "www.example.com", "olga@example.com", "olga@example.com"),
        ("jane@example.com", "example.com", "olga@example.net", None),
        ("jane.acme@gmail.com", "acme.example.test", "olga.acme@gmail.com", None),
        (JANE, "acme.example.test", f" {JANE.upper()} ", None),
        (JANE, "acme.example.test", "mailer-daemon@acme.example.test", None),
        (JANE, "acme.example.test", "acme.example.test", None),
        (JANE, "acme.example.test", "", None),
    ],
    ids=[
        "same-domain",
        "case-and-spaces",
        "subdomain",
        "lead-mailbox-domain",
        "site-by-registered-name",
        "other-zone",
        "free-mail-of-the-lead",
        "the-lead-himself",
        "mail-system",
        "not-an-address",
        "empty",
    ],
)
def test_who_counts_as_a_colleague_on_the_company_domain(
    lead: str, host: str, sender: str, to: str | None
) -> None:
    """Домен компании — сайт компании или домен рабочего адреса лида, их поддомены, а сайт — и по
    зарегистрированному имени (`mail.of_company`, то же правило, что у автоответа мёртвого
    ящика); бесплатная почта доменом компании не бывает, даже если на ней сам лид."""
    assert mail.colleague_of(sender, _dialog(lead, host)) == to


# --- стоп-лист судит адрес коллеги ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("held", "words"),
    [("sales", "в ручном стоп-листе продаж"), ("asked", "стоп-лист, причина «unsubscribed»")],
    ids=["sales-stop-list", "unsubscribed-from-donor-letters"],
)
async def test_a_colleague_in_the_stop_list_gets_no_answer_in_words(
    session: AsyncSession, answered: Answered, held: str, words: str
) -> None:
    """Ответ уходит коллеге — стоп-лист судит его адрес: ручной стоп-лист продаж и общий по
    правилу продаж. Отписка коллеги от писем доноров держит и продажи (`holds_sales`), а общая
    проверка отправки строку чужого этапа не видит — отказ даёт модуль продаж."""
    await _from(session, answered, COLLEAGUE)
    if held == "sales":
        session.add(SalesStoplistModel(email=COLLEAGUE))
    else:
        session.add(
            SuppressionModel(
                email=COLLEAGUE,
                stage=Stage.DONORS,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by="тест",
            )
        )
    await session.flush()

    said = f"коллеге лида {COLLEAGUE} писать нельзя: {words}"
    with pytest.raises(LeadStoppedError, match=re.escape(said)):
        await answered.answer(session)

    assert len(_seen(answered.source)) == 1  # только первое письмо


async def test_the_lead_in_the_stop_list_still_holds_the_answer_to_a_colleague(
    session: AsyncSession, answered: Answered
) -> None:
    """Лиду писать нельзя — диалог закрыт для писем продаж целиком: коллеге ответ тоже не уходит
    (то же правило `unwritable`, что до решения)."""
    await _from(session, answered, COLLEAGUE)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()

    with pytest.raises(LeadStoppedError, match=f"лиду {JANE} писать нельзя"):
        await answered.answer(session)

    assert len(_seen(answered.source)) == 1


# --- события доставки и стоп-лист находят письмо коллеге -----------------------------------------


async def test_delivery_events_of_the_answer_to_the_colleague_are_ours(
    session: AsyncSession, answered: Answered
) -> None:
    """Отказ доставки ответа коллеге — о нашем письме: адрес события — тот, кому ответ ушёл."""
    await _from(session, answered, COLLEAGUE)
    outcome = await answered.answer(session)

    report = await apply_events(
        session, [DeliveryEvent("bounce", outcome.message_id, COLLEAGUE.upper())], now=w.NOW
    )

    assert (report.bounced, report.unknown) == (1, 0)
    letter = await session.get(MessageModel, outcome.message_id)
    assert letter is not None
    assert letter.status is MessageStatus.BOUNCED


async def test_threads_of_an_address_name_the_dialog_where_the_answer_went_to_it(
    session: AsyncSession, answered: Answered
) -> None:
    """Переписки адресата (стоп-листу и сверке событий): коллеге ответили — его переписка; ещё не
    отвечали — писем ему нет, и переписки нет."""
    thread = answered.first.thread_id
    await _from(session, answered, COLLEAGUE)
    assert await mail.threads_to(session, COLLEAGUE) == [], "коллеге ещё не отвечали"

    await answered.answer(session)

    assert await mail.threads_to(session, f" {COLLEAGUE.upper()} ") == [thread]
    assert await mail.threads_to(session, JANE) == [thread], "переписка лида — его по-прежнему"


async def test_an_answer_that_went_to_the_lead_makes_no_addressee_of_the_writer(
    session: AsyncSession, answered: Answered
) -> None:
    """Граница: написал чужой адрес, ответ ушёл лиду — у чужого адреса переписок нет."""
    elsewhere = "olga@beta.example.test"
    await _from(session, answered, elsewhere)

    await answered.answer(session)

    assert await mail.threads_to(session, elsewhere) == []


async def test_the_stop_list_takes_an_answer_to_the_colleague_off_the_queue(
    session: AsyncSession, answered: Answered
) -> None:
    """Ответ коллеге ждёт в очереди (почта его однажды не приняла), а коллега попал в стоп-лист:
    стоп-лист снимает письма адресу по переписке, где ответ уходит ему (`threads_to`). Цепочку
    лида приём остановил ещё на ответе человека — в переписке ждёт только ответ."""
    await _from(session, answered, COLLEAGUE)
    first = answered.first
    await ReplyRepository(session).stop_chain(first.thread_id)
    queued = MessageModel(
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
    session.add(queued)
    await session.flush()

    assert await stoplist.stop_pending(session, email=COLLEAGUE) == 1

    assert queued.status is MessageStatus.STOPPED
