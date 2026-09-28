"""«Пишите на X@» из автоответа мёртвого ящика.

Правило «мёртвый ящик» хоронит адрес, которому мы писали. Автоответ такого
ящика часто сам называет следующий — и адрес на домене донора ложится
в контакты следующим по порядку, а адрес на чужом домене (gmail и т. п.)
уходит человеку словами: написать туда без человека значит написать
незнакомому от имени нашего домена.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.config import outreach as outreach_cfg
from backend.features.contacts.manual import MANUAL_SCORE
from backend.features.contacts.preference import preferred_first
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.replies import redirect
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.shared.sliding_window import SlidingWindow
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "travelnotes.co.uk"
DEAD = f"john@{HOST}"
AUTO = {"Auto-Submitted": "auto-replied"}
OURS = "mail-a.example.com"


# --- правило без базы --------------------------------------------------------


def _sort(*addresses: str, known: tuple[str, ...] = ()) -> redirect.Redirect:
    return redirect.sort_out(addresses, sender=DEAD, host=HOST, ours=[OURS], known=known)


def test_address_on_the_donors_site_goes_to_contacts() -> None:
    got = _sort(f"editor@{HOST}", f"ads@mail.{HOST}")

    assert got.added == (f"editor@{HOST}", f"ads@mail.{HOST}")
    assert got.review_reason is None


def test_address_elsewhere_goes_to_a_human_with_the_address_in_words() -> None:
    got = _sort("jane.doe@gmail.com")

    assert got.added == ()
    assert got.elsewhere == ("jane.doe@gmail.com",)
    assert got.review_reason is not None
    assert "jane.doe@gmail.com" in got.review_reason
    assert "добавить руками в карточке донора" in got.review_reason


def test_look_alike_registered_domain_is_not_the_donors_site() -> None:
    """Сравнивается зарегистрированное имя, а не хвост строки: «notes.co.uk»
    и «travelnotes.co.uk» — разные сайты."""
    assert _sort("editor@notes.co.uk").elsewhere == ("editor@notes.co.uk",)
    assert _sort(f"editor@{HOST}.evil.com").elsewhere == (f"editor@{HOST}.evil.com",)


@pytest.mark.parametrize(
    ("address", "why"),
    [
        (DEAD, "отправителя"),
        (f"noreply@{HOST}", "робот"),
        (f"mailer-daemon@{HOST}", "робот"),
        (f"anna+m417.7d3a91c2e5@replies.{OURS}", "наш"),
        (f"anna@{OURS}", "наш"),
        (f"anna@replies.{OURS}", "наш"),
        (f"privacy@{HOST}", "чужой отдел"),
    ],
)
def test_what_is_not_the_donors_next_address(address: str, why: str) -> None:
    got = _sort(address)

    assert got.added == ()
    assert got.elsewhere == ()
    assert [(dropped, reason) for dropped, reason in got.dropped if why in reason], got.dropped


def test_our_label_is_ours_on_any_domain() -> None:
    """Автоответ, повторивший наше «куда отвечать», позвал бы нас писать
    самим себе — метка узнаётся без сверки домена."""
    got = _sort("anna+m12.0123456789@replies.elsewhere.org")

    assert got.dropped
    assert "наш" in got.dropped[0][1]


def test_known_address_is_not_added_twice() -> None:
    got = _sort(f"editor@{HOST}", known=(f"EDITOR@{HOST}",))

    assert got.added == ()
    assert "уже есть" in got.dropped[0][1]


def test_directory_in_an_auto_reply_is_not_copied_whole() -> None:
    """Автоответ со справочником редакции записал бы донору десятки адресов
    выше найденного лестницей: берём первые, остальные — в лог."""
    named = [f"editor{number}@{HOST}" for number in range(1, 8)]

    got = _sort(*named)

    assert got.added == tuple(named[: redirect.MAX_NAMED])
    assert len(got.dropped) == len(named) - redirect.MAX_NAMED
    assert all("больше" in why for _, why in got.dropped)


def test_addresses_come_from_the_hand_written_part_and_reply_to() -> None:
    """Цитата — наше письмо и наш адрес; Reply-To — законное «пишите сюда»."""
    incoming = Incoming(
        message_id="<a@b>",
        to=(),
        from_email=DEAD,
        subject="Automatic reply",
        text=(
            "John has left the company. Please contact Editor@TravelNotes.co.uk.\n\n"
            "On Mon, 28 Sep 2026 at 10:04, Anna <anna@mail-a.example.com> wrote:\n"
            "> write to anna@mail-a.example.com"
        ),
        headers={"Reply-To": "Desk <desk@travelnotes.co.uk>"},
    )

    assert redirect.named_in(incoming) == (f"editor@{HOST}", f"desk@{HOST}")


# --- приём на настоящей базе ----------------------------------------------------


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    """Письмо в ящик, который умер. Рядом — адрес, найденный лестницей,
    и наш ящик отправки."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    dead = ContactModel(domain_id=domain.id, email=DEAD, source=ContactSource.PAGE)
    found = ContactModel(domain_id=domain.id, email=f"info@{HOST}", source=ContactSource.PAGE)
    session.add_all([dead, found])
    session.add(
        SenderModel(
            domain=OURS,
            email=f"anna@{OURS}",
            stage=Stage.DONORS,
            daily_cap=20,
            status=SenderStatus.FREE,
        )
    )
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=dead.id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=dead.id,
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


def dead_mailbox(message: MessageModel, text: str, **headers: str) -> Incoming:
    return Incoming(
        message_id="<dead-1@travelnotes.co.uk>",
        to=(reply_to.address_for(message.id, sender_email=f"anna@{OURS}", secret=SECRET),),
        from_email=DEAD,
        subject="Re: Advertising rates",
        text=text,
        headers={**AUTO, **headers},
    )


def _no_queue() -> None:
    raise AssertionError("приём мёртвого ящика не должен ставить задач в очередь")


async def _contacts(session: AsyncSession, message: MessageModel) -> list[ContactModel]:
    rows = await session.execute(
        select(ContactModel)
        .where(ContactModel.domain_id == message.domain_id)
        .order_by(*preferred_first())
    )
    return list(rows.scalars().all())


async def test_named_address_becomes_the_next_one(
    session: AsyncSession, sent: MessageModel
) -> None:
    """Адрес из автоответа — первым в порядке адресов донора: выше найденного
    лестницей и выше похороненного."""
    got = await Inbox(session, now=NOW).accept(
        dead_mailbox(
            sent,
            "This mailbox is no longer monitored. Please write to editor@travelnotes.co.uk.",
        )
    )
    await session.flush()

    assert got.kind is ReplyKind.BOUNCE
    assert got.forwarding.added == (f"editor@{HOST}",)
    assert not got.needs_review
    order = await _contacts(session, sent)
    first = order[0]
    assert first.email == f"editor@{HOST}"
    assert first.source is ContactSource.MANUAL
    assert first.verification_status == redirect.FROM_AUTOREPLY
    assert first.verification_score == MANUAL_SCORE
    dead = next(contact for contact in order if contact.email == DEAD)
    assert dead.verification_status == "bounced", "похороненный адрес остаётся похороненным"


async def test_foreign_address_waits_for_a_human(
    session: AsyncSession, sent: MessageModel, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="backend.features.replies")
    got = await Inbox(session, now=NOW).accept(
        dead_mailbox(sent, "John has left the company. Please contact jane.doe@gmail.com.")
    )
    await session.flush()

    assert got.needs_review
    assert got.review_reason is not None
    assert "jane.doe@gmail.com" in got.review_reason
    assert "jane.doe@gmail.com" not in [contact.email for contact in await _contacts(session, sent)]
    # Поручение человеку — в лог адресом целиком: по маске его не вписать.
    assert any("jane.doe@gmail.com" in record.getMessage() for record in caplog.records)


async def test_reply_to_header_names_the_address(session: AsyncSession, sent: MessageModel) -> None:
    got = await Inbox(session, now=NOW).accept(
        dead_mailbox(sent, "This address is no longer in use.", **{"Reply-To": f"desk@{HOST}"})
    )
    await session.flush()

    assert got.forwarding.added == (f"desk@{HOST}",)


async def test_our_own_and_known_addresses_are_not_taken(
    session: AsyncSession, sent: MessageModel
) -> None:
    """Наш ящик отправки, метка ответа и адрес, который у донора уже есть, —
    мимо контактов; уникальность базы тоже не срабатывает."""
    got = await Inbox(session, now=NOW).accept(
        dead_mailbox(
            sent,
            f"This mailbox is no longer monitored. Write to info@{HOST}, anna@{OURS} "
            f"or anna+m{sent.id}.0123456789@replies.{OURS}.",
        )
    )
    await session.flush()

    assert got.forwarding.added == ()
    assert len(got.forwarding.dropped) == 3
    assert len(await _contacts(session, sent)) == 2


async def test_only_the_dead_mailbox_rule_follows_addresses(
    session: AsyncSession, sent: MessageModel
) -> None:
    """Отчёт почты о недоставке называет адреса — но не «пишите сюда»:
    их не берём."""
    got = await Inbox(session, now=NOW).accept(
        Incoming(
            message_id="<ndr-1@mx.travelnotes.co.uk>",
            to=(reply_to.address_for(sent.id, sender_email=f"anna@{OURS}", secret=SECRET),),
            from_email=f"mailer-daemon@mx.{HOST}",
            subject="Mail",
            text=f"550 5.1.1 <{DEAD}>: user unknown. Contact postmaster@{HOST} or admin@{HOST}.",
        )
    )
    await session.flush()

    assert got.kind is ReplyKind.BOUNCE
    assert got.rule != "мёртвый ящик"
    assert got.forwarding == redirect.Redirect()


async def test_webhook_reports_it_in_words(
    client: AsyncClient,
    session: AsyncSession,
    sent: MessageModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отчёт приёма — ответ вебхука: причина словами, с адресом целиком."""
    monkeypatch.setattr(inbound_routes, "_throttle", SlidingWindow())
    # Отказ доставки разбора не ставит; тронь приём очередь — тест упадёт,
    # а не уйдёт в живой Redis.
    monkeypatch.setattr(inbound_routes, "runs_queue", _no_queue)
    label = reply_to.address_for(sent.id, sender_email=f"anna@{OURS}", secret=SECRET)

    response = await client.post(
        "/api/inbound/replies",
        data={
            "from": f"John <{DEAD}>",
            "to": label,
            "subject": "Automatic reply: Advertising rates",
            "text": f"I have left the company. Please contact jane.doe@gmail.com or editor@{HOST}.",
            "headers": "Message-ID: <dead-2@travelnotes.co.uk>\nAuto-Submitted: auto-replied",
        },
        headers={"X-Inbound-Secret": SECRET},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "bounce"
    assert body["needs_review"]
    assert "jane.doe@gmail.com" in body["reason"]
    emails = [contact.email for contact in await _contacts(session, sent)]
    assert f"editor@{HOST}" in emails
