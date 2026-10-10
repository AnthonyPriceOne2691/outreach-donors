"""Адрес робота не становится адресом донора.

Письмо от noreply с суммой или файлом правила вида считают ответом
человека — и это верно: цену в нём разберут (без них это автоответчик). Но
адрес, с которого ответили, запоминается предпочтительным: следующее
письмо донору ушло бы роботу, который его выбросит, а отказ доставки
ударил бы по нашему домену. Правило «адрес робота» одно на весь приём
(`replies/robots.py`): им же вид ответа узнаёт письма почты.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ContactSource, DonorStatus, MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import reply_to
from backend.features.replies import classify, robots
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.features.replies.repository import ReplyRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "travelnotes.co.uk"
WROTE_TO = f"editor@{HOST}"


@pytest.mark.parametrize(
    "address",
    [
        "noreply@donor.test",
        "no-reply@donor.test",
        "no_reply+42@donor.test",
        "No.Reply@donor.test",
        "donotreply@donor.test",
        "do-not-reply@donor.test",
        "noreply-bounces@donor.test",
        "mailer-daemon@mx.donor.test",
        "MAILER-DAEMON@mx.donor.test",
        "postmaster@donor.test",
        "mail.daemon@donor.test",
        # Проверка прода 10.10.2026: службы подписывают уведомления «<служба>-noreply@».
        "accounts-noreply@service.example.test",
        "sc-noreply@service.example.test",
        "payments.no-reply@service.example.test",
    ],
)
def test_robot_addresses(address: str) -> None:
    assert robots.robot(address)


@pytest.mark.parametrize(
    "address",
    [
        "editor@donor.test",
        "info@donor.test",
        "noreplyteam@donor.test",
        "info.noreplyteam@donor.test",
        "juno-reply@donor.test",
        "replies@donor.test",
    ],
)
def test_people_are_not_robots(address: str) -> None:
    """Сверка — целым словом: «noreplyteam» — чья-то команда, а не робот,
    и терять её адрес дороже; «juno-reply» — имя, а не «no-reply»."""
    assert not robots.robot(address)


def test_one_rule_serves_the_kind_of_reply_too() -> None:
    """Правило одно: письмо от postmaster с текстом отказа — отказ доставки,
    а от noreply — «ящик не читается» о себе, не о доноре."""
    bounce = classify.classify(
        Incoming(
            message_id="<b@x>",
            to=(),
            from_email="postmaster@donor.test",
            subject="Mail",
            text="Delivery has failed to these recipients or groups.",
        )
    )
    assert bounce.kind is ReplyKind.BOUNCE


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=WROTE_TO, source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        internet_message_id="<ours-1@mail.test>",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def reply_from(message: MessageModel, sender: str, text: str) -> Incoming:
    return Incoming(
        message_id=f"<{sender}-1@site.test>",
        to=(reply_to.address_for(message.id, sender_email="anna@mail.test", secret=SECRET),),
        from_email=sender,
        subject="Re: Advertising rates",
        text=text,
    )


async def _addresses(session: AsyncSession, message: MessageModel) -> dict[str, datetime | None]:
    rows = await session.execute(
        select(ContactModel.email, ContactModel.last_replied_at).where(
            ContactModel.domain_id == message.domain_id
        )
    )
    return dict(rows.tuples().all())


@pytest.mark.parametrize("robot", ["noreply@travelnotes.co.uk", "mailer-daemon@travelnotes.co.uk"])
async def test_robot_reply_is_a_reply_but_not_an_address(
    session: AsyncSession, sent: MessageModel, robot: str
) -> None:
    """Ответ робота без служебных фраз — ответ: его разберут и увидит
    человек. Адресом донора робот не становится, и адрес, которому мы
    писали, остаётся первым."""
    got = await Inbox(session, now=NOW).accept(
        reply_from(sent, robot, "Our sponsored post rate is 150 GBP.")
    )
    await session.flush()

    assert got.kind is ReplyKind.HUMAN
    assert got.parse_pending
    assert await _addresses(session, sent) == {WROTE_TO: None}


async def test_person_answering_from_another_mailbox_is_still_remembered(
    session: AsyncSession, sent: MessageModel
) -> None:
    """Обратная сторона правила: человек с другого ящика — по-прежнему
    предпочтительный адрес. Защита от робота не должна задеть его."""
    await Inbox(session, now=NOW).accept(
        reply_from(sent, f"elena@{HOST}", "Our sponsored post rate is 150 GBP.")
    )
    await session.flush()

    assert (await _addresses(session, sent))[f"elena@{HOST}"] == NOW


async def test_repository_itself_refuses_a_robot(session: AsyncSession, sent: MessageModel) -> None:
    """Запрет стоит в самом запоминании, а не только в конвейере: любой
    следующий вызов упрётся в то же правило."""
    got = await ReplyRepository(session).remember_answering_address(
        domain_id=sent.domain_id, email="do-not-reply@travelnotes.co.uk", now=NOW
    )

    assert got is None
    assert "do-not-reply@travelnotes.co.uk" not in await _addresses(session, sent)
