"""«Не пишем» по ссылке — края, найденные ревью 12.10.2026.

Основной путь (`tests/test_advertiser_withdraw.py`) снимает письмо под снятую ссылку
в момент решения. Здесь — как письмо под ссылку, которой у рекламодателя больше нет,
всё-таки могло уйти, и чем это закрыто:

- повторный обход сменил страницу снятого кандидата — письмо под прежнюю не совпадало;
- письмо вернули в очередь после неизвестного исхода — оно могло уже уйти, и пометка
  «не уходило» дала бы тому же человеку второе первое письмо;
- решение, сказанное до того, как стало доходить до рекламодателя, — до кнопки
  «Перевести» сборка и отправка ему писали;
- проверка при отправке — последний рубеж для всего, что разошлось во времени.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.crawl import review
from backend.features.crawl.niche import NICHE
from backend.features.crawl.promote import apply_rejections, promote
from backend.features.letters.attempts import LINK_WITHDRAWN
from backend.features.letters.followups import Chain
from backend.features.letters.recipients import Recipients
from backend.features.letters.sending import Sending, StalePriceError, WithdrawnLinkError
from backend.features.letters.transport import NullTransport
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_advertiser_withdraw import BRAND, BY, OTHER, _advertiser, _bought, _promoted
from tests.test_letters_advertisers import (
    DONOR,
    build_offers,
    donor_of_the_link,
    offers,
    priced_donor,
    stage_two_sender,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def operator(
    make_user: Callable[..., Awaitable[UserModel]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user(BY, role=UserRole.OPERATOR)
    return await sign_in(BY)


async def _two_donors(
    session: AsyncSession,
) -> tuple[CandidateModel, CandidateModel, MessageModel]:
    """Рекламодатель с двумя донорами: письмо в очереди — под ссылку сильного."""
    await priced_donor(session)
    await priced_donor(session, OTHER)
    strong = await _bought(session, points=9)
    weak = await _bought(session, donor=OTHER, points=5)
    await _promoted(session)
    await build_offers(session)
    (letter,) = await offers(session)
    return strong, weak, letter


class TestTheLinkMoved:
    async def test_a_recrawled_page_does_not_keep_the_old_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Повторный обход сменил страницу кандидата, письмо — под прежнюю. «Не пишем»
        снимает письма не по снятой странице, а по действующей: всё, что не под неё."""
        strong, weak, letter = await _two_donors(session)
        old_page = strong.best_page_url
        strong.best_page_url = f"https://{DONOR}/another-article/"
        await session.flush()

        decided = await review.decide(session, strong.id, confirmed=False, by=BY)
        rebuilt = await build_offers(session)

        await session.refresh(letter)
        assert old_page in (letter.body or "")
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
        assert decided.withdrawn.stopped == 1
        assert rebuilt.prepared == 1
        fresh = (await offers(session))[-1]
        assert weak.best_page_url in (fresh.body or "")

    async def test_sending_stops_a_letter_under_a_link_that_changed(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Последний рубеж — отправка: лучшая ссылка рекламодателя сменилась после сборки
        (пересчёт и перевод), письмо под прежнюю не уходит, а останавливается своей
        причиной — и следующая сборка пишет под действующую."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        candidate.best_page_url = f"https://{DONOR}/another-article/"
        await session.flush()
        await promote(session)
        await session.commit()

        with pytest.raises(WithdrawnLinkError, match="больше нет"):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)
        rebuilt = await build_offers(session)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
        assert rebuilt.prepared == 1
        assert "another-article" in ((await offers(session))[-1].body or "")


class TestALetterThatMayHaveLeft:
    async def test_a_letter_back_in_the_queue_stays_written(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Вернуть в очередь» после неизвестного исхода, затем «не пишем»: у письма наш
        `Message-ID` прежней попытки — оно могло дойти. Останавливается как «писали»:
        адрес не освобождается, диалог остаётся, второго первого письма нет."""
        strong, _, letter = await _two_donors(session)
        thread = letter.thread_id
        letter.internet_message_id = "<offer-1@offers.test>"
        await session.flush()

        await review.decide(session, strong.id, confirmed=False, by=BY)
        rebuilt = await build_offers(session)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, None)
        assert letter.thread_id == thread
        assert await session.get(ThreadModel, thread) is not None
        assert rebuilt.prepared == 0


class TestTheThread:
    async def test_a_busy_thread_stays_and_the_reminder_takes_the_sent_subject(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """В диалоге снятого письма есть ещё что-то (здесь — автоответ): диалог не
        удаляется, и новое письмо ложится в него. Тема добивки — у ушедшего письма,
        а не у снятого (`followups.Chain._first_subject`)."""
        strong, _, letter = await _two_donors(session)
        thread = letter.thread_id
        session.add(
            ReplyModel(
                thread_id=thread, kind=ReplyKind.AUTO_REPLY, raw_body="I am away until Monday"
            )
        )
        await session.flush()

        await review.decide(session, strong.id, confirmed=False, by=BY)
        await build_offers(session)
        fresh = (await offers(session))[-1]
        await stage_two_sender(session)
        await Sending(session, NullTransport(), now=NOW).send(fresh.id)
        await session.refresh(letter)
        await session.refresh(fresh)

        assert await session.get(ThreadModel, thread) is not None
        assert letter.thread_id == thread
        assert fresh.thread_id == thread
        assert letter.subject != fresh.subject
        subject = await Chain(session, now=NOW)._first_subject(thread)
        assert subject == fresh.subject


class TestOldDecisions:
    async def test_the_build_applies_an_old_rejection_first(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Не пишем», записанное до того, как стало доходить до рекламодателя: сборка
        доводит его сама — без кнопки «Перевести» и консоли."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        candidate.confirmed = False  # как записывали решения до 11.10.2026
        await session.flush()

        report = await build_offers(session)

        assert report.prepared == 0
        assert await _advertiser(session) is None

    async def test_the_build_repicks_the_link_of_an_old_rejection(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        strong, weak, letter = await _two_donors(session)
        strong.confirmed = False
        await session.flush()

        report = await build_offers(session)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
        assert report.prepared == 1
        assert weak.best_page_url in ((await offers(session))[-1].body or "")
        assert await apply_rejections(session) == 0, "доведённое второй раз не трогается"

    async def test_sending_holds_an_old_rejection_without_a_build(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Сборки после выкатки ещё не было, а пачка уже идёт: письмо под ссылку,
        о которой сказали «не пишем», не уходит и останавливается."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        candidate.confirmed = False
        await session.commit()

        with pytest.raises(WithdrawnLinkError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)


class TestNicheBusiness:
    async def test_a_niche_business_loses_only_the_offer_by_link(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Бизнес ниши со «пишем», которому собрали и оффер по найденной ссылке:
        «не пишем» по ссылке снимает оффер по ссылке, а решение по бизнесу и его
        строка остаются; доводить потом нечего."""
        await priced_donor(session)
        await _niche_business(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)

        await review.decide(session, candidate.id, confirmed=False, by=BY)
        again = await build_offers(session)

        advertiser = await _advertiser(session)
        await session.refresh(letter)
        assert advertiser is not None
        assert (advertiser.source, advertiser.confirmed_by_human) == (NICHE, True)
        assert (advertiser.best_page_url, advertiser.best_anchor) == (None, None)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
        assert again.prepared == 0
        assert await apply_rejections(session) == 0


async def _niche_business(session: AsyncSession) -> None:
    """Бизнес ниши со «пишем» человека — строка рекламодателя без ссылки."""
    domain = DomainModel(host=BRAND)
    session.add(domain)
    await session.flush()
    session.add(
        AdvertiserModel(
            domain_id=domain.id,
            source=NICHE,
            confirmed_by_human=True,
            decided_at=NOW,
            decided_by=BY,
        )
    )
    await session.flush()


class TestTheOrder:
    """Отказы отправки — от главного (`offer_standing.py`): снят рекламодатель → снята
    ссылка → цена донора."""

    async def test_a_withdrawn_link_goes_before_a_stale_price(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ссылку сняли, и цена донора к тому же устарела: письмо останавливается как
        письмо под снятую ссылку — перезапрос цены его бы не спас."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        candidate.confirmed = False
        (await donor_of_the_link(session)).last_price_at = NOW - timedelta(days=200)
        await session.commit()

        with pytest.raises(WithdrawnLinkError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)

    async def test_a_niche_business_written_by_link_needs_a_fresh_price(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Бизнесу ниши с найденной ссылкой оффер собирает рассылка «по ссылке» — при
        свежей цене донора, и отправка перепроверяет ту же цену: решает аудитория
        рассылки, как у сборки, а не то, откуда рекламодатель взялся."""
        await priced_donor(session)
        await _niche_business(session)
        await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        (await donor_of_the_link(session)).last_price_at = NOW - timedelta(days=200)
        await session.flush()

        with pytest.raises(StalePriceError, match="устарела или снята"):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED


class TestTheScreenWarns:
    async def test_a_reply_is_said_out_loud(self, session: AsyncSession) -> None:
        """Рекламодатель уже ответил: «не пишем» переписку не останавливает, и это
        сказано рядом с решением — экрану и журналу."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        session.add(ReplyModel(thread_id=letter.thread_id, kind=ReplyKind.HUMAN, raw_body="Hi!"))
        await session.flush()

        decided = await review.decide(session, candidate.id, confirmed=False, by=BY)
        quiet = await review.decide(session, candidate.id, confirmed=True, by=BY, force=True)

        assert decided.answered is True
        assert decided.warning is not None
        assert decided.warning.startswith(f"{BRAND} уже ответил")
        assert quiet.warning is None, "«пишем» предупреждать не о чем"

    async def test_the_route_answers_with_the_warning(
        self, session: AsyncSession, client: AsyncClient, operator: str
    ) -> None:
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        session.add(ReplyModel(thread_id=letter.thread_id, kind=ReplyKind.HUMAN, raw_body="Hi!"))
        await session.commit()

        response = await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": False},
            headers=bearer(operator),
        )

        assert response.status_code == 200, response.text
        assert response.json()["warning"].startswith(f"{BRAND} уже ответил")


class TestTheDonorCard:
    async def test_a_withdrawn_offer_is_not_a_previous_address(self, session: AsyncSession) -> None:
        """Сайт — и донор, и рекламодатель. Снятое ссылкой письмо оффера не уходило:
        карточка донора не говорит «прошлый адрес не дошёл»."""
        domain = await make_donor(session, "both.example.test", email="editor@both.example.test")
        campaign = CampaignModel(name="Офферы", stage=Stage.ADVERTISERS, status="draft")
        session.add(campaign)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=domain.id,
                step=0,
                status=MessageStatus.STOPPED,
                failure_reason=LINK_WITHDRAWN,
                subject="Your placement",
                body="Hi",
                idempotency_key="advertisers:both.example.test:0",
            )
        )
        await session.flush()

        address = await Recipients(session).letter_address(domain.id)

        assert address.contact_id is not None
        assert address.note is None
