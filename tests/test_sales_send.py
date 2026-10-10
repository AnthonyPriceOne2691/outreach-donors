"""Отправка писем продаж общей почтой на настоящей базе — срез 4.6b, T2–T3.

Своей отправки у продаж нет: письмо уходит общей отправкой (`Sending`), пачкой — общим
`send_queue` этапа продаж, добивки — общим проходом, ящик — общим балансировщиком по
этапу. Транспорт подставной (нулевой с памятью, адреса `*.example.test`): живых писем нет.
Мир — подключённые продажи и очередь, собранная сборкой продаж (`sales/queue.py`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import ContactSource, MessageStatus, SenderStatus, Stage, UserRole
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters import followups
from backend.features.letters.batch import send_queue
from backend.features.letters.sending import NoSenderError, NotReadyError, Sending
from backend.features.sales import chain, chain_text, queue
from backend.features.sales.mail import LeadStoppedError
from backend.features.sales.models import (
    LeadStatus,
    SalesHandoffModel,
    SalesLeadModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording

JANE = "jane@acme.example.test"
OLGA = "olga@acme.example.test"
SIGNED = f"\n\n{w.SIGNATURE}\n\n{w.ADDRESS}"
#: Первое письмо ушло — через три дня добивка, ещё через пять — вторая (рассылка [3, 5]).
FIRST_DUE = w.NOW + timedelta(days=3, minutes=1)
SECOND_DUE = FIRST_DUE + timedelta(days=5, minutes=1)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _queued(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Лиды гипотезы и их первые письма — сборкой продаж."""
    for email in emails or (JANE,):
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10)
    rows = await session.scalars(select(MessageModel).order_by(MessageModel.id))
    return list(rows)


def _transports() -> _ByStage:
    return _ByStage(sales=Recording(), donors=Recording(), advertisers=Recording())


def _seen(source: _ByStage, stage: str = "sales") -> list[Any]:
    return list(source.transports[stage].seen)  # type: ignore[attr-defined]


# --- A1: письмо уходит общей отправкой ------------------------------------------------------


async def test_a1_letter_goes_to_the_lead_from_the_sales_box_with_list_unsubscribe(
    session: AsyncSession, world: w.World
) -> None:  # A1
    [letter] = await _queued(session, world)
    source = _transports()

    outcome = await Sending(session, source, now=w.NOW).send(letter.id)

    [outgoing] = _seen(source)
    assert source.asked == ["sales"]
    assert outcome.sender_email == w.SALES_BOX
    assert (outgoing.to, outgoing.from_email, outgoing.from_name) == (
        JANE,
        w.SALES_BOX,
        w.SENDER_NAME,
    )
    assert outgoing.from_name != outreach_cfg.SENDER_NAME
    assert outgoing.subject == "A made-up question for Example Test Co"
    assert outgoing.body.endswith(SIGNED)
    assert outgoing.unsubscribe_url.startswith("https://unsub.example.test/u/u")
    assert outgoing.in_reply_to is None
    await session.refresh(letter)
    assert letter.status is MessageStatus.SENT
    assert letter.sender_id == world.sales_box.id
    assert letter.next_action_at == w.NOW + timedelta(days=3)


async def test_recipient_is_the_lead_even_when_the_domain_has_a_contact(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «адрес из contacts вместо лида»: у домена компании есть адрес сайта."""
    [letter] = await _queued(session, world)
    session.add(
        ContactModel(
            domain_id=letter.domain_id, email="info@acme.example.test", source=ContactSource.PAGE
        )
    )
    await session.flush()
    source = _transports()

    await Sending(session, source, now=w.NOW).send(letter.id)

    assert [outgoing.to for outgoing in _seen(source)] == [JANE]


# --- подключение: без своей учётки письмо продаж не уходит ----------------------------------


async def test_without_own_account_sales_do_not_go_through_the_shared_one(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без своего ключа механизм направления отдал бы общую учётку — письмо продаж ушло
    бы ключом доноров. Отказ — до выбора учётки этапа: набор транспортов не спрошен."""
    [letter] = await _queued(session, world)
    monkeypatch.delenv("OUTREACH_SALES_SENDGRID_API_KEY")
    monkeypatch.setattr(outreach_cfg, "SENDGRID_API_KEY", "SG.made-up-shared-key")
    source = _transports()

    with pytest.raises(SalesNotConnectedError, match="нет своей учётки почты продаж"):
        await Sending(session, source, now=w.NOW).send(letter.id)

    assert source.asked == []
    await session.refresh(letter)
    assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)


async def test_a2_address_removed_after_assembly_stops_the_letter(
    session: AsyncSession, world: w.World
) -> None:  # A2
    [letter] = await _queued(session, world)
    await w.settings(session, physical_address=None)
    source = _transports()

    with pytest.raises(SalesNotConnectedError) as refused:
        await Sending(session, source, now=w.NOW).send(letter.id)

    assert str(refused.value).startswith(
        f"Письмо №{letter.id}: {SALES_NOT_CONNECTED} — не задан физический адрес"
    )
    assert source.asked == []
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


async def test_incomplete_chain_of_the_letter_set_holds_the_letter(
    session: AsyncSession, world: w.World
) -> None:
    """Цепочку набора первого письма выключили — добивок не будет: первое не уходит. Отказ —
    письму, а не этапу: пачка его считает и идёт дальше (`test_sales_mail_seams.py`, A3)."""
    [letter] = await _queued(session, world)
    off = chain_text.step_template(step=3, language="en", body=w.FOLLOW_BODY[3], active=False)
    await chain.save(session, off, hypothesis_id=None, author="тест", author_id=None)

    with pytest.raises(NotReadyError, match="нет второй добивки"):
        await Sending(session, _transports(), now=w.NOW).send(letter.id)


# --- повторная сверка готового письма --------------------------------------------------------


@pytest.mark.parametrize(
    ("spoil", "words"),
    [
        (lambda body: body.replace(w.ADDRESS, "Old Street 3, Oldtown"), "в конце письма нет"),
        (lambda body: body.replace(SIGNED, ""), "в конце письма нет"),
        (lambda body: "Hello {{name}},\n\n" + body, "подстановка без значения: {{name}}"),
    ],
)
async def test_ready_letter_is_checked_again_before_it_goes(
    session: AsyncSession, world: w.World, spoil: Callable[[str], str], words: str
) -> None:
    """Мутант «письмо без физического адреса уходит»: адреса в письме нет — не уходит."""
    [letter] = await _queued(session, world)
    letter.body = spoil(letter.body or "")
    await session.flush()
    source = _transports()

    with pytest.raises(NotReadyError, match=words):
        await Sending(session, source, now=w.NOW).send(letter.id)

    assert _seen(source) == []
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


async def test_a4_first_letter_outside_the_corridor_does_not_go(
    session: AsyncSession, world: w.World
) -> None:  # A4
    [letter] = await _queued(session, world)
    letter.uniqueness_pct = 0.05
    await session.flush()

    with pytest.raises(NotReadyError, match="ниже коридора"):
        await Sending(session, _transports(), now=w.NOW).send(letter.id)


# --- кому писать больше нельзя ---------------------------------------------------------------


async def _lead_of(session: AsyncSession, letter: MessageModel) -> SalesLeadModel:
    link = await session.get(SalesThreadModel, letter.thread_id)
    assert link is not None
    found = await session.get(SalesLeadModel, link.lead_id)
    assert found is not None
    return found


@pytest.mark.parametrize("how", ["rejected", "handed_off", "stoplist"])
async def test_lead_that_must_not_be_written_stops_the_letter(
    session: AsyncSession, world: w.World, how: str
) -> None:
    [letter] = await _queued(session, world)
    lead = await _lead_of(session, letter)
    if how == "rejected":
        lead.status = LeadStatus.REJECTED
    elif how == "handed_off":
        session.add(SalesHandoffModel(thread_id=letter.thread_id, lead_id=lead.id))
    else:
        session.add(SalesStoplistModel(host="acme.example.test"))
    await session.flush()

    with pytest.raises(LeadStoppedError):
        await Sending(session, _transports(), now=w.NOW).send(letter.id)

    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


# --- пачка: общий send_queue этапа продаж ------------------------------------------------------


async def test_a3_batch_of_sales_sends_both_leads_of_one_company(
    session: AsyncSession, world: w.World
) -> None:  # A3
    letters = await _queued(session, world, JANE, OLGA)
    source = _transports()

    report = await send_queue(session, source, stage=Stage.SALES)

    assert (report.sent, dict(report.refused), report.stopped, report.left) == (2, {}, None, 0)
    assert sorted(outgoing.to for outgoing in _seen(source)) == [JANE, OLGA]
    assert len({letter.idempotency_key for letter in letters}) == 2


async def test_batch_counts_a_stopped_lead_and_goes_on(
    session: AsyncSession, world: w.World
) -> None:
    await _queued(session, world, JANE, OLGA)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()
    source = _transports()

    report = await send_queue(session, source, stage=Stage.SALES)

    assert (report.sent, dict(report.refused)) == (1, {"стоп-лист": 1})
    assert [outgoing.to for outgoing in _seen(source)] == [OLGA]


async def test_sales_letters_take_only_sales_boxes(session: AsyncSession, world: w.World) -> None:
    """Ящик выбирает общий балансировщик по этапу: ящик доноров письму продаж не дают."""
    [letter] = await _queued(session, world)
    world.sales_box.enabled = False
    await session.flush()

    with pytest.raises(NoSenderError, match="Сегодня писать некому"):
        await Sending(session, _transports(), now=w.NOW).send(letter.id)


# --- добивки: общий проход, та же переписка ---------------------------------------------------


async def _first_sent(
    session: AsyncSession, world: w.World, *emails: str
) -> tuple[_ByStage, list[MessageModel]]:
    letters = await _queued(session, world, *emails)
    source = _transports()
    for letter in letters:
        await Sending(session, source, now=w.NOW).send(letter.id)
    return source, letters


async def test_followups_go_in_the_same_thread_with_the_first_subject(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «добивка продаж с темой»: тема добивки — первого письма, своей у шага нет."""
    source, [first] = await _first_sent(session, world)

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.waiting, report.postponed) == (1, 0, 0)
    _, outgoing = _seen(source)
    assert outgoing.subject == first.subject
    assert outgoing.in_reply_to == first.internet_message_id
    assert (outgoing.to, outgoing.from_email) == (JANE, w.SALES_BOX)
    assert outgoing.body == f"A made-up first reminder for Jane.{SIGNED}"
    step = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
    assert step is not None
    assert step.thread_id == first.thread_id
    assert step.idempotency_key == f"sales:acme.example.test:{JANE}:1"
    assert step.next_action_at == FIRST_DUE + timedelta(days=5)


async def test_last_followup_ends_the_chain(session: AsyncSession, world: w.World) -> None:
    source, _ = await _first_sent(session, world)
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    report = await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)

    assert report.sent == 1
    last = _seen(source)[-1]
    assert last.body == f"A made-up last reminder about Example Test Co.{SIGNED}"
    step = await session.scalar(select(MessageModel).where(MessageModel.step == 2))
    assert step is not None
    assert step.idempotency_key == f"sales:acme.example.test:{JANE}:2"
    assert step.next_action_at is None


async def test_two_leads_of_one_company_get_their_own_followups(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «ключ без контакта»: вторая добивка компании упёрлась бы в ключ первой."""
    source, _ = await _first_sent(session, world, JANE, OLGA)

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert report.sent == 2
    keys = await session.scalars(
        select(MessageModel.idempotency_key).where(MessageModel.step == 1).order_by(MessageModel.id)
    )
    assert list(keys) == [f"sales:acme.example.test:{JANE}:1", f"sales:acme.example.test:{OLGA}:1"]


async def test_handed_off_lead_gets_no_followup_the_chain_stops(
    session: AsyncSession, world: w.World
) -> None:
    """Шов 5.3: лид передан телемаркетологу — добивки ему не идут."""
    source, [first] = await _first_sent(session, world)
    lead = await _lead_of(session, first)
    session.add(SalesHandoffModel(thread_id=first.thread_id, lead_id=lead.id))
    await session.flush()

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.stopped) == (0, 1)
    assert len(_seen(source)) == 1
    step = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
    assert step is not None
    assert step.status is MessageStatus.STOPPED
    await session.refresh(first)
    assert first.next_action_at is None


async def test_switched_off_sales_keep_the_followup_deadline_and_say_so(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, [first] = await _first_sent(session, world)
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.waiting) == (0, 1)
    await session.refresh(first)
    assert first.next_action_at == w.NOW + timedelta(days=3)


async def test_followup_keeps_the_set_of_the_first_letter(
    session: AsyncSession, world: w.World
) -> None:
    """Гипотеза завела свою цепочку после первого письма: добивка — общего набора, как
    первое письмо; шаги наборов не смешиваются."""
    source, _ = await _first_sent(session, world)
    own = {2: "[reminder] fixed\nAn own made-up reminder of the hypothesis."}
    await w.chain_of(session, hypothesis_id=world.hypothesis_id)
    for step, body in own.items():
        made = chain_text.step_template(step=step, language="en", body=body)
        await chain.save(
            session, made, hypothesis_id=world.hypothesis_id, author="т", author_id=None
        )

    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert _seen(source)[-1].body == f"A made-up first reminder for Jane.{SIGNED}"


async def test_followup_waiting_for_a_box_gets_todays_signature(
    session: AsyncSession, world: w.World
) -> None:
    """Добивка не ушла (ящик на паузе) и ждёт в базе; подпись тем временем сменили —
    следующая попытка уходит с нынешней, а не застревает на сверке."""
    source, _ = await _first_sent(session, world)
    world.sales_box.status = SenderStatus.PAUSED
    await session.flush()
    first_try = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    world.sales_box.status = SenderStatus.FREE
    await w.settings(session, signature="Mira Testova\nAnother Made-up Agency")

    second_try = await followups.send_due(
        session, transport=source, limit=5, now=FIRST_DUE + followups.POSTPONE
    )

    assert (first_try.postponed, second_try.sent) == (1, 1)
    assert _seen(source)[-1].body.endswith(
        f"\n\nMira Testova\nAnother Made-up Agency\n\n{w.ADDRESS}"
    )


# --- экран: одиночная отправка и пачка — те же маршруты, что у доноров -----------------------


async def test_screen_sends_one_sales_letter(
    session: AsyncSession, world: w.World, client: AsyncClient, admin_token: str
) -> None:
    [letter] = await _queued(session, world)
    await session.commit()

    response = await client.post(f"/api/letters/{letter.id}/send", headers=bearer(admin_token))

    assert response.status_code == 200, response.text
    assert response.json()["sender_email"] == w.SALES_BOX


async def test_screen_says_in_words_why_sales_are_not_connected(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [letter] = await _queued(session, world)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    response = await client.post(f"/api/letters/{letter.id}/send", headers=bearer(admin_token))

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Письмо №{letter.id}: {SALES_NOT_CONNECTED} — модуль продаж выключен — включает "
        "администратор"
    )


async def test_batch_button_takes_the_sales_stage(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кнопка пачки d6 уже знает этап: очередь продаж считается и ставится этапом продаж."""
    await _queued(session, world, JANE, OLGA)
    await session.commit()
    jobs = _Jobs()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs)

    response = await client.post(
        "/api/letters/send-queue", json={"stage": "sales"}, headers=bearer(admin_token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["queued"] == 2
    assert [args[1] for args in jobs.enqueued] == ["sales"]


class _Jobs:
    def __init__(self) -> None:
        self.enqueued: list[tuple[object, ...]] = []

    def enqueue(self, *args: object, **_: object) -> object:
        self.enqueued.append(args)
        return type("Job", (), {"id": "job-1"})()


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[Any]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("admin@sales-send.example.test", role=UserRole.ADMIN)
    return await sign_in("admin@sales-send.example.test")


def test_sales_box_model_is_the_shared_one() -> None:
    """Ящики продаж — общие `senders` с этапом, своего балансировщика нет."""
    assert SenderModel.__tablename__ == "senders"
