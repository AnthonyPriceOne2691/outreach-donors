"""Адрес лида в списках почты: «Диалоги», очередь писем, «Исход неизвестен».

Строки `contacts` у переписки продаж нет (решение 01.10): адрес живёт у лида, лид — у переписки по
явной связи, и списки, бравшие адрес только из `contacts`, у каждой переписки продаж писали «адрес
не определён». Теперь адрес лида называет модуль продаж мостом (`stages.sales_addresses`) — одним
вопросом на список, а не на строку. Доноры и рекламодатели — как были. Модуль не подключён или
упал — у строк продаж пусто, а список стоит. У письма адрес лида — только у письма цепочки: ответ
в переписке уходит лиду или его коллеге, и кому — решает отправка.

Модуль продаж здесь подставной, своим классом; последние тесты — с настоящим, на настоящей базе и
через экраны. Адреса выдуманы.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import ContactSource, MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import unknown_outcome
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.outreach.repository import EVERY_STAGE, OutreachRepository
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_sales_stage_mail import admin_token

__all__ = ["admin_token"]  # фикстура — отсюда её видит pytest

JANE = "jane@acme.example.test"
#: Адрес сайта донора — строка `contacts`.
EDITOR = "editor@site.example.test"
#: Коллега лида, которому ушёл ответ в переписке (решение 13.10).
BOB = "bob@acme.example.test"


def _later() -> datetime:
    """«Сейчас» списка зависших: письма отданы почте дольше пяти минут назад (`STUCK_AFTER`)."""
    return datetime.now(UTC) + timedelta(hours=1)


@dataclass
class _Leads:
    """Подставной модуль продаж: знает адреса лидов и помнит, о каких переписках его спросили."""

    leads: dict[int, str] = field(default_factory=dict)
    broken: bool = False
    asked: list[list[int]] = field(default_factory=list)

    async def addresses(self, _session: AsyncSession, thread_ids: Sequence[int]) -> dict[int, str]:
        self.asked.append(list(thread_ids))
        if self.broken:
            raise RuntimeError("выдуманная поломка модуля: addresses")
        return {number: self.leads[number] for number in thread_ids if number in self.leads}


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> _Leads:
    """Подставной модуль подключён тем же входом, что настоящий; после теста — прежний."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = _Leads()
    stages.register_sales(lambda: found)
    return found


@dataclass(frozen=True, slots=True)
class World:
    """Переписка продаж (строки `contacts` нет) и переписка доноров (адрес — строка `contacts`),
    у каждой — первое письмо."""

    sales: ThreadModel
    donor: ThreadModel
    first: MessageModel
    donor_first: MessageModel


def _letter(
    campaign: CampaignModel,
    thread: ThreadModel,
    contact_id: int | None,
    status: MessageStatus,
    *,
    step: int = FIRST_STEP,
    answers: int | None = None,
) -> MessageModel:
    return MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=thread.domain_id,
        contact_id=contact_id,
        step=step,
        status=status,
        subject="A made-up question",
        body="A made-up letter.",
        answers_reply_id=answers,
        idempotency_key=f"lead-address:{thread.id}:{step}",
    )


async def _world(session: AsyncSession, status: MessageStatus = MessageStatus.QUEUED) -> World:
    sales_campaign = CampaignModel(name="Адреса: продажи", stage=Stage.SALES, status="running")
    donors_campaign = CampaignModel(name="Адреса: доноры", stage=Stage.DONORS, status="running")
    acme = DomainModel(host="acme.example.test")
    session.add_all([sales_campaign, donors_campaign, acme])
    site = await make_donor(session, "site.example.test", email=EDITOR)
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == site.id)
    )
    sales = ThreadModel(domain_id=acme.id, campaign_id=sales_campaign.id)
    donor = ThreadModel(domain_id=site.id, campaign_id=donors_campaign.id, contact_id=contact_id)
    session.add_all([sales, donor])
    await session.flush()
    first = _letter(sales_campaign, sales, None, status)
    donor_first = _letter(donors_campaign, donor, contact_id, status)
    session.add_all([first, donor_first])
    await session.flush()
    return World(sales, donor, first, donor_first)


async def _answer(session: AsyncSession, world: World, status: MessageStatus) -> MessageModel:
    """Наш ответ в переписке продаж на письмо коллеги лида."""
    reply = ReplyModel(
        thread_id=world.sales.id,
        message_id=world.first.id,
        kind=ReplyKind.HUMAN,
        raw_body="Please write to me instead.",
        from_email=BOB,
    )
    session.add(reply)
    await session.flush()
    campaign = await session.get(CampaignModel, world.sales.campaign_id)
    assert campaign is not None
    answer = _letter(campaign, world.sales, None, status, step=ANSWER_STEP, answers=reply.id)
    session.add(answer)
    await session.flush()
    return answer


# --- мост ------------------------------------------------------------------------------------


async def test_the_bridge_asks_once_without_repeats_and_in_order(
    session: AsyncSession, module: _Leads
) -> None:
    """Номера — без повторов и по порядку: модуль читает их пачками."""
    module.leads = {3: JANE}

    found = await stages.sales_addresses(session, [5, 3, 5])

    assert found == {3: JANE}
    assert module.asked == [[3, 5]]


async def test_nothing_to_ask_is_not_asked(session: AsyncSession, module: _Leads) -> None:
    assert await stages.sales_addresses(session, []) == {}
    assert module.asked == []


async def test_without_the_module_the_bridge_answers_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stages._SALES, "load", None)

    assert await stages.sales_addresses(session, [3]) == {}


async def test_a_broken_module_answers_nothing_and_is_said_in_the_log(
    session: AsyncSession, module: _Leads, caplog: pytest.LogCaptureFixture
) -> None:
    module.broken = True

    with caplog.at_level(logging.INFO, logger=stages.__name__):
        found = await stages.sales_addresses(session, [3])

    assert found == {}
    assert "ошибка модуля продаж: выдуманная поломка модуля: addresses" in caplog.text
    assert "мост продаж: Адреса лидов переписок продаж — " in caplog.text


# --- «Диалоги» -------------------------------------------------------------------------------


async def test_dialogs_list_names_the_lead_and_keeps_the_donor_contact(
    session: AsyncSession, module: _Leads
) -> None:
    """Одним вопросом на весь список и только о переписках продаж без адреса."""
    world = await _world(session)
    module.leads = {world.sales.id: JANE}

    rows = await OutreachRepository(session).threads(stages=EVERY_STAGE)

    shown = {row.thread.id: row.contact_email for row in rows}
    assert shown == {world.sales.id: JANE, world.donor.id: EDITOR}
    assert module.asked == [[world.sales.id]]


async def test_dialog_card_names_the_lead_and_a_donor_card_does_not_ask(
    session: AsyncSession, module: _Leads
) -> None:
    world = await _world(session)
    module.leads = {world.sales.id: JANE}
    repository = OutreachRepository(session)

    sales = await repository.thread(world.sales.id)
    asked = list(module.asked)
    donor = await repository.thread(world.donor.id)

    assert (sales.row.contact_email, donor.row.contact_email) == (JANE, EDITOR)
    assert asked == module.asked == [[world.sales.id]]


async def test_a_sales_dialog_with_a_contact_keeps_it(
    session: AsyncSession, module: _Leads
) -> None:
    """Строка `contacts` у переписки продаж бывает (переписки до решения 01.10): её адрес —
    как есть, модуль о ней не спрошен."""
    world = await _world(session)
    contact = ContactModel(
        domain_id=world.sales.domain_id, email="info@acme.example.test", source=ContactSource.PAGE
    )
    session.add(contact)
    await session.flush()
    world.sales.contact_id = contact.id
    await session.flush()
    module.leads = {world.sales.id: JANE}

    rows = await OutreachRepository(session).threads(stages=EVERY_STAGE)

    assert {row.thread.id: row.contact_email for row in rows}[world.sales.id] == contact.email
    assert module.asked == []


# --- письма ----------------------------------------------------------------------------------


async def test_letters_queue_and_letter_card_name_the_lead(
    session: AsyncSession, module: _Leads
) -> None:
    world = await _world(session)
    module.leads = {world.sales.id: JANE}
    repository = LetterRepository(session)

    [queued] = await repository.queued(stage=Stage.SALES)
    card = await repository.letter(world.first.id)
    [donor] = await repository.queued(stage=Stage.DONORS)

    assert (queued.message.id, queued.email, card.email) == (world.first.id, JANE, JANE)
    assert (donor.message.id, donor.email) == (world.donor_first.id, EDITOR)
    assert module.asked == [[world.sales.id], [world.sales.id]]


async def test_stuck_chain_letter_names_the_lead_and_a_stuck_answer_does_not(
    session: AsyncSession, module: _Leads
) -> None:
    """По «Кому» письмо ищут в журнале платформы. Ответ ушёл коллеге лида (решение 13.10) — адрес
    лида увёл бы поиск не туда, и у ответа «Кому» не называется, как раньше."""
    world = await _world(session, MessageStatus.SENDING)
    answer = await _answer(session, world, MessageStatus.SENDING)
    module.leads = {world.sales.id: JANE}

    sales = await unknown_outcome.stuck(session, stage=Stage.SALES, now=_later())
    donors = await unknown_outcome.stuck(session, stage=Stage.DONORS, now=_later())

    assert {letter.message.id: letter.email for letter in sales} == {
        world.first.id: JANE,
        answer.id: None,
    }
    assert [(letter.message.id, letter.email) for letter in donors] == [
        (world.donor_first.id, EDITOR)
    ]
    assert module.asked == [[world.sales.id]]


# --- без модуля и с поломкой: списки стоят ----------------------------------------------------


@pytest.mark.parametrize("how", ["unregistered", "broken"])
async def test_without_an_answer_the_lists_stand_and_sales_stay_unknown(
    session: AsyncSession, module: _Leads, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    if how == "unregistered":
        monkeypatch.setattr(stages._SALES, "load", None)
    else:
        module.broken = True
    world = await _world(session)
    dialogs = OutreachRepository(session)
    letters = LetterRepository(session)

    rows = await dialogs.threads(stages=EVERY_STAGE)
    card = await dialogs.thread(world.sales.id)
    [queued] = await letters.queued(stage=Stage.SALES)
    [donor] = await letters.queued(stage=Stage.DONORS)

    assert {row.thread.id: row.contact_email for row in rows} == {
        world.sales.id: None,
        world.donor.id: EDITOR,
    }
    assert (card.row.contact_email, queued.email, donor.email) == (None, None, EDITOR)


# --- настоящий модуль продаж ------------------------------------------------------------------


async def _linked(session: AsyncSession, world: World) -> None:
    """Лид переписки продаж — по явной связи, как её заводит сборка очереди продаж."""
    hypothesis = SalesHypothesisModel(name="Адреса: гипотеза")
    session.add(hypothesis)
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=world.sales.domain_id,
        email=JANE,
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    session.add(lead)
    await session.flush()
    session.add(
        SalesThreadModel(
            thread_id=world.sales.id,
            lead_id=lead.id,
            language="en",
            chain_version="chain-made-up-12",
        )
    )
    await session.flush()


async def test_the_real_sales_module_names_the_lead_in_every_list(session: AsyncSession) -> None:
    assert stages.sales_registered()
    world = await _world(session)
    await _linked(session, world)

    rows = await OutreachRepository(session).threads(stages=EVERY_STAGE)
    card = await OutreachRepository(session).thread(world.sales.id)
    [queued] = await LetterRepository(session).queued(stage=Stage.SALES)

    assert {row.thread.id: row.contact_email for row in rows} == {
        world.sales.id: JANE,
        world.donor.id: EDITOR,
    }
    assert (card.row.contact_email, queued.email) == (JANE, JANE)


async def test_screens_show_the_lead_address_of_a_sales_dialog_and_letter(
    session: AsyncSession, client: AsyncClient, admin_token: str
) -> None:
    """Экран «Диалоги» — список и карточка — и «Исход неизвестен» продаж: адрес лида, а не
    «адрес не определён». Модуль продаж — настоящий."""
    world = await _world(session, MessageStatus.SENDING)
    await _linked(session, world)
    # Отдано почте двенадцать минут назад: исход решает человек (`STUCK_AFTER`).
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == world.first.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=12))
    )
    await session.commit()
    headers = bearer(admin_token)

    listed = await client.get("/api/threads", headers=headers)
    one = await client.get(f"/api/threads/{world.sales.id}", headers=headers)
    stuck = await client.get("/api/letters/unknown?stage=sales", headers=headers)

    assert listed.status_code == one.status_code == stuck.status_code == 200
    shown = {row["id"]: row["contact_email"] for row in listed.json()}
    assert shown == {world.sales.id: JANE, world.donor.id: EDITOR}
    assert one.json()["card"]["contact_email"] == JANE
    assert [(row["id"], row["email"]) for row in stuck.json()["letters"]] == [
        (world.first.id, JANE)
    ]
