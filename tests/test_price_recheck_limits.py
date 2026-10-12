"""Перезапрос цены: лимиты ящика, скорость списка, чужой этап и мелочи проверки 12.10.2026.

- **Лимиты ящика.** Перезапрос в переписку считается и в дневном лимите ящика, и в часовом
  лейне писем переписки — пачка по двадцати нажатий иначе давала всплеск с одного ящика.
  Не влезает — отказ словами до заведения письма. Новому письму нужен свободный ящик.
- **Скорость.** Список протухших — одним заходом фактов: запросов столько же при двух
  донорах, сколько при шести.
- **Чужой этап.** Письмо лиду продаж в словах отказа называется только тому, кто видит
  продажи.
- **Мелочи:** номер попытки ключа — по этапу; собранный раньше перезапрос правится только
  в очереди; тема ветки без темы ответа; подтверждённая цена автоответа — слово донора;
  у рассылки перезапросов в очереди писем нет добивок и не меряется отличие; срок повтора
  не меньше суток.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.api.letters.schemas import QueuedLetterCard
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import price_recheck
from backend.features.letters.followups import Chain
from backend.features.letters.price_recheck import RecheckRefusedError, Way
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import Sending
from backend.features.outreach.repository import OutreachRepository
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.recheck_world import BOX, HOST, Recording, World, manual_stale, stale_conversation

pytestmark = pytest.mark.usefixtures("filled_legal")

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
def moment() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def world(session: AsyncSession, moment: datetime) -> World:
    return await stale_conversation(session, moment)


async def _sent(
    session: AsyncSession, world: World, *, step: int, at: datetime, key: str
) -> MessageModel:
    """Письмо с ящика переписки в другую переписку того же ящика — ушедшее в `at`."""
    other = ThreadModel(domain_id=world.donor.domain_id, campaign_id=world.thread.campaign_id)
    session.add(other)
    await session.flush()
    letter = MessageModel(
        campaign_id=world.thread.campaign_id,
        thread_id=other.id,
        domain_id=world.donor.domain_id,
        sender_id=world.box.id,
        step=step,
        status=MessageStatus.DELIVERED,
        sent_at=at,
        idempotency_key=key,
    )
    session.add(letter)
    await session.commit()
    return letter


class TestMailboxLimits:
    async def test_the_daily_cap_of_the_thread_box_refuses_before_the_letter(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        world.box.daily_cap = 1
        await _sent(session, world, step=0, at=moment - timedelta(minutes=5), key="today:0")

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal == (
            f"Ящик {BOX} выбрал дневной лимит (1 из 1): перезапрос уходит с ящика переписки "
            "и уйдёт с него завтра."
        )

    async def test_the_hourly_lane_of_the_thread_box_refuses_before_the_letter(
        self,
        session: AsyncSession,
        world: World,
        moment: datetime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "FOLLOWUP_PER_SENDER_PER_HOUR", 1)
        await _sent(session, world, step=1, at=moment - timedelta(minutes=10), key="hour:1")

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal == (
            f"Ящик {BOX} выбрал часовой запас писем переписки (1 из 1): перезапрос уйдёт "
            "с него через час."
        )

    async def test_a_sent_recheck_counts_in_the_daily_cap_and_the_hourly_lane(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Счёт один у отправки, прохода добивок и перезапроса: следующему письму с ящика
        перезапрос виден и в дневном лимите, и в часовом лейне."""
        await price_recheck.ask(
            session,
            Sending(session, Recording(), now=moment),
            world.donor.id,
            author_id=None,
            now=moment,
        )

        daily = await OutreachRepository(session).sent_today(now=moment, first_only=True)
        assert daily[world.box.id] == 1
        assert await Chain(session, now=moment).sent_this_hour(world.box.id) == 1

    async def test_a_new_letter_needs_a_free_box_today(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        donor = await manual_stale(session, moment, host="manual.example.test")
        box = await make_sender(session, BOX)
        box.enabled = False
        await session.commit()

        found = await price_recheck.look(session, donor.id, now=moment)

        assert found.refusal is not None
        assert found.refusal.startswith("Сегодня писать некому:")


class TestSpeed:
    async def test_the_list_costs_the_same_for_two_donors_and_for_six(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        """Факты списка читаются разом: запросов к базе не больше при шести донорах, чем
        при двух (проверка 12.10.2026: около пятнадцати на каждого)."""
        await make_sender(session, BOX)

        async def counted(hosts: list[str]) -> int:
            for index, host in enumerate(hosts):
                if index % 2:
                    await manual_stale(session, moment, host=host)
                else:
                    await stale_conversation(session, moment, host=host)
            with _statements(session) as sent:
                listed = await price_recheck.stale(session, limit=500, now=moment)
            assert {row.host for row in listed.rows} >= set(hosts)
            return len(sent)

        two = await counted(["a.example.test", "b.example.test"])
        six = await counted([f"{letter}.example.test" for letter in "cdefgh"])

        assert six == two


@contextmanager
def _statements(session: AsyncSession) -> Iterator[list[str]]:
    """Запросы, ушедшие в базу соединением теста."""
    sent: list[str] = []

    def spy(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        sent.append(statement)

    target = session.bind.sync_connection  # type: ignore[union-attr]
    event.listen(target, "before_cursor_execute", spy)
    try:
        yield sent
    finally:
        event.remove(target, "before_cursor_execute", spy)


async def _sales_letter(session: AsyncSession, domain_id: int, moment: datetime) -> int:
    """Письмо лиду продаж того же сайта — ушло и живо."""
    campaign = CampaignModel(name="Продажи", stage=Stage.SALES, status="draft")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain_id,
        step=0,
        status=MessageStatus.SENT,
        sent_at=moment - timedelta(days=2),
        idempotency_key="sales:manual.example.test:lead:0",
    )
    session.add(letter)
    await session.commit()
    return letter.id


class TestOtherStage:
    async def test_a_sales_letter_is_named_only_to_who_sees_sales(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        donor = await manual_stale(session, moment, host="manual.example.test")
        await make_sender(session, BOX)
        letter_id = await _sales_letter(session, donor.domain_id, moment)

        hidden = await price_recheck.look(session, donor.id, now=moment, sales_visible=False)
        shown = await price_recheck.look(session, donor.id, now=moment, sales_visible=True)

        assert hidden.refusal == (
            "manual.example.test уже писали в другой роли: одному адресату письмо в другой "
            "роли не уходит."
        )
        assert shown.refusal is not None
        assert f"как лиду продаж (письмо №{letter_id})" in shown.refusal

    async def test_without_the_sales_right_the_screen_does_not_name_it(
        self,
        session: AsyncSession,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        moment: datetime,
    ) -> None:
        donor = await manual_stale(session, moment, host="manual.example.test")
        await make_sender(session, BOX)
        await _sales_letter(session, donor.domain_id, moment)
        await make_user("nosales@team.example.test", permissions={"sales": False})
        await make_user("admin@team.example.test", role=UserRole.ADMIN)

        hidden = await client.get(
            f"/api/price-rechecks/{donor.id}",
            headers=bearer(await sign_in("nosales@team.example.test")),
        )
        shown = await client.get(
            f"/api/price-rechecks/{donor.id}",
            headers=bearer(await sign_in("admin@team.example.test")),
        )

        assert "продаж" not in hidden.json()["refusal"]
        assert "№" not in hidden.json()["refusal"]
        assert "лиду продаж" in shown.json()["refusal"]


class TestDetails:
    async def test_the_attempt_in_the_key_counts_first_letters_of_the_stage(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        """Номер попытки — по первым письмам этапа доноров (`attempts.attempt_number`):
        не дошедшее письмо этому сайту как рекламодателю номер не сдвигает."""
        donor = await manual_stale(session, moment, host="manual.example.test")
        await make_sender(session, BOX)
        offers = CampaignModel(name="Офферы", stage=Stage.ADVERTISERS, status="draft")
        session.add(offers)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=offers.id,
                domain_id=donor.domain_id,
                step=0,
                status=MessageStatus.BOUNCED,
                sent_at=moment - timedelta(days=40),
                idempotency_key="advertisers:manual.example.test:0",
            )
        )
        await session.commit()

        asked = await price_recheck.ask(
            session,
            Sending(session, Recording(), now=moment),
            donor.id,
            author_id=None,
            now=moment,
        )

        letter = await session.get(MessageModel, asked.message_id)
        assert letter is not None
        assert letter.idempotency_key == "donors:manual.example.test:0"

    async def test_a_waiting_recheck_is_rewritten_only_while_still_queued(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Собранный раньше перезапрос правится условной записью: взятый отправкой
        секундой раньше — отказ словами, а не правка письма в пути."""
        found = await price_recheck.look(session, world.donor.id, now=moment)
        assert found.plan is not None
        taken = MessageModel(
            campaign_id=world.thread.campaign_id,
            thread_id=world.thread.id,
            domain_id=world.donor.domain_id,
            step=200,
            status=MessageStatus.SENDING,
            idempotency_key=f"donors:{HOST}:recheck:1",
        )
        session.add(taken)
        await session.commit()

        with pytest.raises(RecheckRefusedError, match="уже не в очереди"):
            await price_recheck._materialize(
                session, replace(found, queued_id=taken.id), found.plan
            )

    async def test_without_a_subject_of_the_answer_the_branch_takes_ours(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        world.reply.subject = None
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.subject == f"Re: Guest article on {HOST}"

    async def test_a_confirmed_price_of_an_auto_reply_is_the_donor_word(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Цена из автоответа с суммой, подтверждённая человеком, — слово донора: донор
        не «молчащий», и перезапрос уходит в его переписку."""
        world.reply.kind = ReplyKind.AUTO_REPLY
        world.reply.reviewed_at = moment - timedelta(days=199)
        world.reply.reviewed_by = "anna@ours.example.test"
        world.reply.price_white = Decimal("90")
        await session.commit()

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.refusal is None
        assert found.plan is not None
        assert found.plan.way is Way.THREAD

    async def test_the_recheck_campaign_shows_no_followups_and_measures_nothing(
        self,
        session: AsyncSession,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        moment: datetime,
    ) -> None:
        """Письмо рассылки перезапросов в очереди писем (почта однажды отказала): добивок
        у него нет, и правка не меряет его отличие от первого письма."""
        donor = await manual_stale(session, moment, host="manual.example.test")
        campaign = await price_recheck._campaign(session)
        thread = ThreadModel(domain_id=donor.domain_id, campaign_id=campaign.id)
        session.add(thread)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=donor.domain_id,
                step=0,
                status=MessageStatus.QUEUED,
                subject="Guest article on manual.example.test: current rate?",
                body="Hi,\n\nWhat is your current price per article?\n\nBest regards,\nAnna Ro",
                idempotency_key="donors:manual.example.test:0",
            )
        )
        await session.commit()
        [row] = await LetterRepository(session).queued(stage=Stage.DONORS)
        assert QueuedLetterCard.of(row).followups == []
        await make_user("admin@team.example.test", role=UserRole.ADMIN)

        edited = await client.patch(
            f"/api/letters/{row.message.id}",
            json={"subject": "Your current rate?", "body": "Hi,\n\nWhat is your rate now?"},
            headers=bearer(await sign_in("admin@team.example.test")),
        )

        assert edited.status_code == 200, edited.text
        assert (edited.json()["uniqueness"], edited.json()["followups"]) == (None, [])


def test_the_repeat_period_is_at_least_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ноль снял бы срок повтора вовсе — перезапрос по каждому нажатию."""
    monkeypatch.setenv("OUTREACH_PRICE_RECHECK_DAYS", "0")

    with pytest.raises(ValidationError):
        outreach_cfg._Outreach()
