"""Одна роль на сайт — и в минуту отправки (ревью стыков 12.10.2026).

Отбор не берёт домен с живым письмом любого этапа, продажи сверяют другое направление при
очистке лида, но между сборкой и отправкой проходят часы: письмо донору собрано, а продажи
пишут той же компании, или наоборот. Первое письмо любого этапа не уходит, если у домена
живое письмо другого этапа: оно снимается с причиной, называющей направление, и «писали»
не считается; пачка идёт к следующему письму. Добивка и ответ в переписке — начатый
разговор, их правило не держит. Модуль продаж здесь подставной (`FakeSalesMail`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import answers, attempts, batch, site_role
from backend.features.letters.followups import send_due
from backend.features.letters.sending import OtherRoleError, Sending
from backend.features.letters.transport import NullTransport
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.test_sales_stage_bridge import FakeSalesMail, _seen, _transports
from tests.test_sales_stage_mail import sales_world
from tests.thread_letters import BOX, Recording, conversation, first_letter, human_reply

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
HOST = "role.example.test"
WORDS = "сайт в работе у другого направления"


async def _live(
    session: AsyncSession,
    domain_id: int,
    stage: Stage,
    *,
    status: MessageStatus = MessageStatus.SENT,
) -> MessageModel:
    """Живое письмо сайту в роли этапа `stage` — без адреса, как первое письмо продаж."""
    campaign = CampaignModel(stage=stage, name=f"Другая роль: {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain_id,
        step=0,
        status=status,
        subject="Hello",
        body="Hello",
        sent_at=None if status is MessageStatus.QUEUED else NOW - timedelta(days=1),
        idempotency_key=f"{stage.value}:role-{domain_id}:{status.value}",
    )
    session.add(letter)
    await session.flush()
    return letter


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Подставной модуль продаж подключён к мосту почты тем же входом, что настоящий."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail()
    stages.register_sales(lambda: found)
    return found


@pytest.mark.parametrize(
    ("stage", "named"), [(Stage.SALES, "продажи"), (Stage.ADVERTISERS, "рекламодатели")]
)
async def test_a_donor_first_letter_is_withdrawn_while_the_site_is_in_work_elsewhere(
    session: AsyncSession, filled_legal: None, stage: Stage, named: str
) -> None:
    """Письмо не ушло, снято с причиной словами — и «писали» не считается: адрес свободен
    для следующего письма, пустой диалог снят, как у письма под снятую ссылку."""
    letter = await first_letter(session, host=HOST)
    await make_sender(session, BOX)
    await _live(session, letter.domain_id, stage)
    thread_id, contact_id = letter.thread_id, letter.contact_id
    transport = Recording()

    with pytest.raises(OtherRoleError, match=f"{WORDS}: {named} \\({HOST}\\)"):
        await Sending(session, transport, now=NOW).send(letter.id)

    assert transport.seen == []
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, f"{WORDS}: {named}")
    assert letter.failure_reason == attempts.elsewhere_reason(stage)
    assert letter.sender_id is None
    assert await session.get(ThreadModel, thread_id) is None
    fresh = await session.scalar(
        select(ContactModel.id).where(ContactModel.id == contact_id, attempts.fresh(ContactModel))
    )
    assert fresh == contact_id


async def test_a_letter_taken_by_a_sender_meanwhile_is_not_called_withdrawn(
    session: AsyncSession, filled_legal: None
) -> None:
    """Письмо взяла отправка между чтением и снятием: оно не снято, и отказ «снято» был бы
    неправдой — проверка молчит, дальше решает захват отправки (ревью продаж 13.10)."""
    letter = await first_letter(session, host=HOST)
    await _live(session, letter.domain_id, Stage.SALES)
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == letter.id)
        .values(status=MessageStatus.SENDING)
        .execution_options(synchronize_session=False)
    )

    await site_role.check(session, letter, Stage.DONORS, OtherRoleError)

    status, reason = (
        await session.execute(
            select(MessageModel.status, MessageModel.failure_reason).where(
                MessageModel.id == letter.id
            )
        )
    ).one()
    assert (status, reason) == (MessageStatus.SENDING, None)


async def test_the_batch_names_it_and_sends_the_rest(
    session: AsyncSession, filled_legal: None
) -> None:
    """Отказ — наследник стоп-листа: пачка не встаёт, считает письмо словами и идёт дальше."""
    held = await first_letter(session, host=HOST)
    free = await first_letter(session, host=f"free-{HOST}")
    await make_sender(session, BOX)
    await _live(session, held.domain_id, Stage.SALES, status=MessageStatus.QUEUED)
    await session.commit()

    report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

    assert (report.sent, dict(report.refused), report.stopped, report.left) == (
        1,
        {WORDS: 1},
        None,
        0,
    )
    await session.refresh(held)
    await session.refresh(free)
    assert (held.status, free.status) == (MessageStatus.STOPPED, MessageStatus.SENT)


async def test_a_sales_first_letter_is_refused_the_same_way_while_a_donor_letter_lives(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    """Письмо продаж той же компании, которой ушло письмо донору, — тот же отказ, что пачка
    и автоотправка продаж пропускают. Модуль продаж не спрошен о проверке письма, почта —
    не спрошена вовсе. Диалог продаж остаётся: к нему привязан лид, и удалить его значило бы
    вернуть лида в сборку продаж."""
    world = await sales_world(session)
    await session.delete(world.reply)
    await _live(session, world.letter.domain_id, Stage.DONORS)
    source = _transports()

    with pytest.raises(OtherRoleError, match=f"{WORDS}: доноры"):
        await Sending(session, source, now=NOW).send(world.letter.id)

    assert _seen(source) == []
    assert fake.asked == [f"recipient {world.letter.id}"]
    await session.refresh(world.letter)
    assert (world.letter.status, world.letter.failure_reason) == (
        MessageStatus.STOPPED,
        f"{WORDS}: доноры",
    )
    assert world.letter.thread_id == world.thread.id
    assert await session.get(ThreadModel, world.thread.id) is not None


async def test_a_followup_goes_while_the_site_is_in_work_elsewhere(
    session: AsyncSession, filled_legal: None
) -> None:
    """Добивка — начатый разговор: живое письмо другого этапа её не держит."""
    talk = await conversation(session, sent_at=NOW - timedelta(days=10))
    await _live(session, talk.first.domain_id, Stage.SALES, status=MessageStatus.QUEUED)
    assert talk.first.next_action_at is not None

    report = await send_due(
        session, transport=NullTransport(), limit=1, now=talk.first.next_action_at
    )

    assert (report.sent, report.stopped, report.postponed) == (1, 0, 0)


async def test_an_answer_goes_while_the_site_is_in_work_elsewhere(
    session: AsyncSession, filled_legal: None
) -> None:
    """Ответ в переписке — человеку, который написал сам: правило его не держит."""
    talk = await conversation(session, sent_at=NOW - timedelta(days=2))
    reply = await human_reply(session, talk)
    await _live(session, talk.first.domain_id, Stage.SALES)

    sent = await answers.answer_reply(
        session,
        Sending(session, NullTransport(), now=NOW),
        thread_id=talk.thread.id,
        reply_id=reply.id,
        body="Thanks, 90 works for us.",
        author_id=None,
    )

    answer = await session.get(MessageModel, sent.message_id)
    assert answer is not None
    assert answer.status is MessageStatus.SENT


async def test_live_elsewhere_sees_only_a_live_letter_of_another_stage(
    session: AsyncSession,
) -> None:
    """Условие, которое возьмёт себе и модуль продаж: живое письмо другого этапа. Своё
    направление — не «другое»; не дошедшее письмо — не живое."""
    domain = await make_donor(session, HOST)
    sales = await _live(session, domain.id, Stage.SALES)

    def busy(stage: Stage) -> object:
        return select(DomainModel.id).where(
            DomainModel.id == domain.id, attempts.live_elsewhere(DomainModel.id, stage)
        )

    assert await session.scalar(busy(Stage.DONORS)) == domain.id
    assert await session.scalar(busy(Stage.SALES)) is None
    sales.status = MessageStatus.BOUNCED
    await session.flush()
    assert await session.scalar(busy(Stage.DONORS)) is None
