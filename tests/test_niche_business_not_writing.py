"""Сам бизнес ниши — тоже «кому не пишем» (сверка с постановкой 12.10.2026).

Решение 12.10: «кому не пишем» действует на всех путях. Отбор бизнесов ниши
(`letters/niche_recipients.py`) и проверка при отправке сверяли с этим списком только
пример площадки в письме, а сам бизнес — нет: крупному сайту или гос. домену из выдачи
письмо собиралось и уходило, пачкой тоже.

Правило — общее с донорами, на самом сайте (`runs.exclusions.site_not_writing`): имя
домена — без строки донора (судья мог отрезать бизнес до Ahrefs), DR — замер прогона.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.features.core.domain import DonorStatus, MessageStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl.niche import NICHE
from backend.features.donors.verdict import big_site_reason
from backend.features.letters.followups import send_due
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.sending import NotWritingError, Sending
from backend.features.letters.transport import NullTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import NOW, build_offers, offers, priced_donor
from tests.test_letters_advertisers import stage_two_sender as sender
from tests.test_niche_letters import BOOKIE, DONOR, _business, _queued_niche_offer


async def _measured(session: AsyncSession, host: str, *, dr: int) -> None:
    """Прогон, где бизнес нашёлся, мерил его: строка донора с DR, не принятая."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    assert domain is not None
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.UNSUITABLE, dr=dr))
    await session.flush()


class TestTheBuild:
    async def test_a_big_site_from_the_serp_gets_no_offer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)
        await _measured(session, BOOKIE, dr=88)

        report = await build_offers(session, audience=NICHE, campaign_name="Ниша")

        assert report.prepared == 0  # type: ignore[attr-defined]
        assert await NicheRecipients(session).niche_candidates(limit=10) == []
        assert (await NicheRecipients(session).niche_report())["ещё не писали"] == 0

    async def test_a_named_class_needs_no_donor_row(self, session: AsyncSession) -> None:
        """Гос. зона — по имени самого сайта: строки донора у бизнеса нет, судья отрезал
        его до Ahrefs, а правило всё равно держит."""
        await priced_donor(session, DONOR)
        await _business(session)
        business = await session.scalar(select(DomainModel).where(DomainModel.host == BOOKIE))
        assert business is not None
        business.host = "shop.example.gov"
        await session.flush()

        assert await NicheRecipients(session).niche_candidates(limit=10) == []

    async def test_a_small_site_is_written_as_before(self, session: AsyncSession) -> None:
        await priced_donor(session, DONOR)
        await _business(session)
        await _measured(session, BOOKIE, dr=40)

        [candidate] = await NicheRecipients(session).niche_candidates(limit=10)

        assert candidate.host == BOOKIE


class TestTheSending:
    async def test_a_queued_letter_is_stopped_for_good(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Собранное до правила — в «остановлено» с причиной, как письмо донору из списка:
        его снова не напишут, и следующая сборка его не берёт."""
        letter = await _queued_niche_offer(session)
        await _measured(session, BOOKIE, dr=88)

        with pytest.raises(NotWritingError) as refused:
            await Sending(session, NullTransport()).send(letter.id)

        words = (
            f"Письмо №{letter.id} бизнесу ниши {BOOKIE} остановлено: {big_site_reason(80)}. "
            "Добивки не уйдут, и новое письмо ему не соберётся"
        )
        assert str(refused.value) == words
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, words)
        again = await build_offers(session, audience=NICHE, campaign_name="Ниша снова")
        assert again.prepared == 0  # type: ignore[attr-defined]

    async def test_the_chain_ends(self, session: AsyncSession, filled_legal: None) -> None:
        await priced_donor(session, DONOR)
        await _business(session)
        await build_offers(session, audience=NICHE, campaign_name="Ниша", followup_days=(1, 2))
        await sender(session)
        [letter] = await offers(session)
        await Sending(session, NullTransport(), now=NOW).send(letter.id)
        await _measured(session, BOOKIE, dr=88)

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        assert (report.stopped, report.sent) == (1, 0)
