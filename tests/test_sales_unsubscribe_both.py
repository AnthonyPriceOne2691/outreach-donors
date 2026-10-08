"""Отписка словами закрывает и адрес лида; стоп-лист находит письма продаж по адресу лида.

Решение владельца по ревью стыков (B3): «уберите меня» пришло не с адреса лида — ответил
ассистент или второй ящик — закрываются оба адреса, ответившего и лида: стоп-лист без этапа,
письма в очереди и сроки добивок любого направления снимаются для обоих. Ответ с адреса лида,
неуверенная отписка, повтор задачи и доноры — как было.

Письма продаж строки `contacts` не имеют (адрес — у лида), и общий `stop_pending(email=)` их
не находил: теперь находит по переписке лида — вопросом мосту почты (`stages.sales_threads_to`).

Мир — подключённые продажи и очередь, собранная сборкой продаж (`sales/queue.py`): диалог
лида без контакта, как на проде. Транспорт и модель — подставные, сети нет; адреса — на
`*.example.test`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core import stages
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to, stoplist
from backend.features.letters.sending import Sending
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_reply_routing import HOST as LEGACY_HOST
from tests.test_sales_reply_routing import SECRET, FakeClassifier, sales_letter
from tests.test_sales_send import JANE, OLGA, _queued, _transports

HOST = "acme.example.test"
STOP = "Please stop sending these emails."
SURE = KindFound(SalesKind.UNSUBSCRIBE, 0.95, quote=STOP)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _dialogs(session: AsyncSession, world: w.World) -> tuple[MessageModel, MessageModel]:
    """Два лида одной компании — сборкой продаж: письмо Джейн ушло (срок добивки жив),
    письмо коллеги Ольги ещё в очереди. Контакта у писем продаж нет."""
    jane, olga = await _queued(session, world, JANE, OLGA)
    await Sending(session, _transports(), now=w.NOW).send(jane.id)
    await session.refresh(jane)
    assert (jane.status, olga.status) == (MessageStatus.SENT, MessageStatus.QUEUED)
    assert (jane.contact_id, olga.contact_id) == (None, None)
    assert jane.next_action_at is not None
    return jane, olga


async def _donor_letters(
    session: AsyncSession, email: str, host: str = HOST
) -> tuple[MessageModel, MessageModel]:
    """Тот же адрес в рассылке доноров: письмо в очереди и отправленное со сроком добивки."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    assert domain is not None
    contact = await session.scalar(
        select(ContactModel).where(ContactModel.domain_id == domain.id, ContactModel.email == email)
    )
    if contact is None:
        contact = ContactModel(domain_id=domain.id, email=email, source=ContactSource.MANUAL)
        session.add(contact)
    donors = CampaignModel(stage=Stage.DONORS, name=f"Доноры {email}", status="running")
    session.add(donors)
    await session.flush()
    queued = MessageModel(
        campaign_id=donors.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.QUEUED,
        next_action_at=w.NOW + timedelta(hours=3),
        idempotency_key=f"donors:{email}:0",
    )
    sent = MessageModel(
        campaign_id=donors.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=1,
        status=MessageStatus.SENT,
        sent_at=w.NOW - timedelta(days=3),
        next_action_at=w.NOW + timedelta(days=4),
        idempotency_key=f"donors:{email}:1",
    )
    session.add_all([queued, sent])
    await session.flush()
    return queued, sent


async def _answer(
    session: AsyncSession, letter: MessageModel, sender: str, text: str, *, box: str = w.SALES_BOX
) -> ReplyModel:
    """Ответ на письмо — с адреса `sender`, общим приёмом (привязка по метке письма)."""
    local = sender.split("@", 1)[0]
    got = await Inbox(session, now=w.NOW).accept(
        Incoming(
            message_id=f"<{local}-{letter.id}@{HOST}>",
            to=(
                reply_to.address_for(
                    letter.id, sender_email=box, secret=outreach_cfg.INBOUND_SECRET
                ),
            ),
            from_email=sender,
            subject="Re: a made-up question",
            text=text,
        )
    )
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    assert reply.thread_id == letter.thread_id
    return reply


async def _stoplist(session: AsyncSession) -> set[tuple[str | None, Stage | None]]:
    rows = await session.execute(select(SuppressionModel.email, SuppressionModel.stage))
    return {(email, stage) for email, stage in rows.all()}


def _sales(session: AsyncSession, found: KindFound = SURE) -> tuple[SalesReplies, FakeClassifier]:
    model = FakeClassifier(found)
    return SalesReplies(session, model, threshold=0.8, now=w.NOW), model


async def _fresh(session: AsyncSession, *letters: MessageModel) -> None:
    for letter in letters:
        await session.refresh(letter)


# --- ответил не лид: закрыты оба адреса -----------------------------------------------------


@pytest.mark.parametrize(("text", "kind"), [("remove me", "rules"), (STOP, "model")])
async def test_remove_me_from_another_address_closes_both_and_unschedules_both(
    session: AsyncSession, world: w.World, text: str, kind: str
) -> None:
    """«Уберите меня» от коллеги лида в диалоге лида: правилами приёма или видом модели.
    Мутант «закрыть только ответившего» — адрес Джейн открыт, её письма доноров ждут;
    мутант «`stop_pending` без переписок продаж» — письмо продаж Ольге стоит в очереди."""
    jane, olga = await _dialogs(session, world)
    jane_donors = await _donor_letters(session, JANE)
    olga_donors = await _donor_letters(session, OLGA)
    reply = await _answer(session, jane, OLGA, text)
    rules = kind == "rules"
    assert reply.kind is (ReplyKind.UNSUBSCRIBE if rules else ReplyKind.HUMAN)
    sales, model = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    assert await _stoplist(session) == {(OLGA, None), (JANE, None)}, "оба адреса, без этапа"
    await _fresh(session, jane, olga, *jane_donors, *olga_donors)
    assert (olga.status, olga.next_action_at) == (MessageStatus.STOPPED, None), "письмо продаж"
    assert jane.next_action_at is None, "срок добивки лиду снят"
    for queued, sent in (jane_donors, olga_donors):
        assert (queued.status, queued.next_action_at) == (MessageStatus.STOPPED, None)
        assert sent.next_action_at is None
    thread = await session.get(ThreadModel, jane.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.UNSUBSCRIBED
    assert f"закрыты оба адреса, {OLGA} и адрес лида {JANE}" in str(handled.reason)
    assert model.calls == (0 if rules else 1), "отписку правилами модели не отдают"
    if not rules:
        assert (handled.route, handled.waits) == ("unsubscribe", False)


async def test_remove_me_in_a_dialog_without_the_link_closes_the_contact_of_the_dialog(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Диалог продаж, начатый не сборкой (связи `sales_threads` нет): адрес лида — контакт
    диалога. Мутант «адрес лида только по связи» оставил бы его открытым."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    letter = await sales_letter(session)
    assistant = f"assistant@{LEGACY_HOST}"
    reply = await _answer(session, letter, assistant, "remove me", box="sales@mail.example")
    sales, _ = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    assert await _stoplist(session) == {(assistant, None), (f"ceo@{LEGACY_HOST}", None)}
    assert f"адрес лида ceo@{LEGACY_HOST}" in str(handled.reason)


# --- как было: ответ лида, неуверенная отписка, повтор задачи -------------------------------


async def test_remove_me_from_the_lead_closes_the_lead_only_as_before(
    session: AsyncSession, world: w.World
) -> None:
    jane, olga = await _dialogs(session, world)
    jane_donors = await _donor_letters(session, JANE)
    reply = await _answer(session, jane, JANE, "remove me")
    sales, _ = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    assert await _stoplist(session) == {(JANE, None)}
    await _fresh(session, olga, *jane_donors)
    assert olga.status is MessageStatus.QUEUED, "коллеге лида никто не отказывал"
    assert [letter.next_action_at for letter in jane_donors] == [None, None]
    # Свой срок добивки погасил приём; здесь сняты два письма доноров тому же адресу.
    assert handled.reason == "адрес закрыт во всех направлениях, снято с очереди и сроков — 2"


async def test_repeated_job_closes_each_address_once(session: AsyncSession, world: w.World) -> None:
    jane, olga = await _dialogs(session, world)
    reply = await _answer(session, jane, OLGA, "remove me")
    sales, _ = _sales(session)

    first = await sales.handle(reply.id)
    await session.flush()
    again = await sales.handle(reply.id)
    await session.flush()

    rows = await session.scalars(select(SuppressionModel.email).order_by(SuppressionModel.email))
    assert list(rows) == [JANE, OLGA], "по строке на адрес"
    assert str(first.reason).endswith("снято с очереди и сроков — 1")  # письмо продаж Ольге
    assert str(again.reason).endswith("снято с очереди и сроков — 0")
    await _fresh(session, olga)
    assert olga.status is MessageStatus.STOPPED


async def test_unsure_unsubscribe_from_another_address_waits_for_a_human_and_closes_nothing(
    session: AsyncSession, world: w.World
) -> None:
    jane, olga = await _dialogs(session, world)
    reply = await _answer(session, jane, OLGA, STOP)
    sales, _ = _sales(session, KindFound(SalesKind.UNSUBSCRIBE, 0.5, quote=STOP))

    handled = await sales.handle(reply.id)

    await session.flush()
    assert (handled.route, handled.waits) == ("manual", True)
    assert await _stoplist(session) == set()
    await _fresh(session, olga)
    assert olga.status is MessageStatus.QUEUED


# --- общий стоп-лист: письма продаж по адресу лида -------------------------------------------


async def test_stop_list_entry_by_hand_unschedules_the_sales_letter_of_that_lead(
    session: AsyncSession, world: w.World
) -> None:
    """Запись стоп-листа руками на адрес лида снимает его письмо продаж из очереди сразу, а не
    отказом в минуту отправки; письма другого лида не тронуты."""
    jane, olga = await _dialogs(session, world)

    await stoplist.add(session, OLGA.upper(), reason=SuppressionReason.MANUAL, author="тест")
    await session.flush()

    await _fresh(session, jane, olga)
    assert (olga.status, olga.next_action_at) == (MessageStatus.STOPPED, None)
    assert (jane.status, jane.next_action_at is not None) == (MessageStatus.SENT, True)


async def test_stop_pending_without_the_sales_module_finds_what_contacts_find(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модуль продаж не на мосту — писем продаж почта не знает: снимается то, что находят
    строки `contacts`, как до правки."""
    _, olga = await _dialogs(session, world)
    donors = await _donor_letters(session, OLGA)
    monkeypatch.setattr(stages._SALES, "load", None)

    stopped = await stoplist.stop_pending(session, email=OLGA)
    await session.flush()

    assert stopped == len(donors)
    await _fresh(session, olga)
    assert olga.status is MessageStatus.QUEUED


async def test_donors_unchanged_remove_me_from_an_assistant_closes_only_the_sender(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Доноры: отписку решает приём, модуль продаж не зовётся, адрес донора открыт."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    letter = await sales_letter(session, Stage.DONORS)
    elsewhere, _ = await _donor_letters(session, f"ceo@{LEGACY_HOST}", LEGACY_HOST)
    assistant = f"assistant@{LEGACY_HOST}"

    got = await Inbox(session, now=w.NOW).accept(
        Incoming(
            message_id=f"<donor-{letter.id}@{LEGACY_HOST}>",
            to=(reply_to.address_for(letter.id, sender_email="sales@mail.example", secret=SECRET),),
            from_email=assistant,
            subject="Re: A short question",
            text="remove me",
        )
    )

    await session.flush()
    assert (got.kind, got.to_sales) == (ReplyKind.UNSUBSCRIBE, None)
    assert await _stoplist(session) == {(assistant, None)}
    await _fresh(session, elsewhere)
    assert elsewhere.status is MessageStatus.QUEUED
