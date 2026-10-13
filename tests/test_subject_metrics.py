"""Метрики Ahrefs — и в теме письма (сверка с постановкой 12.10.2026).

Тему адресат видит первой, а сборка (`building.py`) и правка человеком (`review.py`)
проверяли на метрики только тело: «DR 45» в теме шаблона рассылки или в поправленной
теме уходил донору и рекламодателю. Нарушение стоит ключа Ahrefs, а не письма.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import guards, review, template
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.guards import ForbiddenContentError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_letters_queue import FakeRewriter

LEAKY_SUBJECT = "Your DR 46 site — a question about rates"


class TestTheGuard:
    def test_the_subject_is_checked_and_named(self) -> None:
        with pytest.raises(ForbiddenContentError, match="В теме письма метрики Ahrefs"):
            guards.assert_letter_clean(LEAKY_SUBJECT, "Hi there,\n\nA question about rates.")

    def test_the_body_is_checked_as_before(self) -> None:
        with pytest.raises(ForbiddenContentError, match="В письме метрики Ahrefs"):
            guards.assert_letter_clean("Rates", "Your organic traffic looks great")

    def test_a_clean_letter_passes(self) -> None:
        guards.assert_letter_clean("Rates for a guest post", "Hi there,\n\nWhat do you charge?")


class TestTheBuild:
    async def test_a_template_subject_with_metrics_builds_nothing(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        leaky = replace(template.default(), subject=LEAKY_SUBJECT)
        builder = QueueBuilder(session, FakeRewriter(), template=leaky)  # type: ignore[arg-type]

        with pytest.raises(ForbiddenContentError, match="В теме письма"):
            await builder.build(BuildRequest(campaign_name="Проверка"))

        letters = await session.scalar(select(func.count(MessageModel.id)))
        assert letters == 0


async def _queued(session: AsyncSession) -> MessageModel:
    domain = await make_donor(session, "one.example.test", email="info@one.example.test")
    campaign = CampaignModel(name="Проверка", stage=Stage.DONORS, status="draft")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Rates",
        body="Hi there,\n\nWhat do you charge for a guest post?",
        idempotency_key="donors:one.example.test:0",
    )
    session.add(letter)
    await session.flush()
    return letter


class TestTheHumanEdit:
    async def test_an_edited_subject_with_metrics_is_refused(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await _queued(session)

        with pytest.raises(ForbiddenContentError, match="В теме письма"):
            await review.edit(
                session,
                letter,
                host="one.example.test",
                subject=LEAKY_SUBJECT,
                body=letter.body or "",
            )

        await session.refresh(letter)
        assert letter.subject == "Rates"

    async def test_a_recheck_letter_is_held_the_same(self, session: AsyncSession) -> None:
        """Перезапрос цены правится без меры отличия (`retext`), а запреты те же."""
        letter = await _queued(session)

        with pytest.raises(ForbiddenContentError, match="В теме письма"):
            await review.retext(session, letter, subject=LEAKY_SUBJECT, body=letter.body or "")

        await session.refresh(letter)
        assert letter.subject == "Rates"
