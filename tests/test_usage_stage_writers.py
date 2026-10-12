"""Кто пишет этап в журнал расхода: поиск адресов — этапом очереди, письма — этапом сборки.

Операции, которые делят этапы, этап называют вызовом (`core/usage.OPERATION_STAGES`):
поиск адресов рекламодателей — трата Этапа 2, а очередь
перепоиска названных доменов общая на этапы и этапа не называет. Проверяется на настоящей
базе тем же путём, каким трата туда попадает.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest
from backend.features.agent import drafting
from backend.features.agent.stages import AGENT_STAGES
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.refind import NamedDomains
from backend.features.contacts.repository import ContactRepository
from backend.features.contacts.search import CONTACTS_OPERATION, search_contacts
from backend.features.core.domain import Stage
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.replies.extract import Extracted
from backend.features.replies.pipeline import Inbox, Parser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_agent_drafting import FakeWriter, agent_on, reply
from tests.test_contacts_no_answer import Paid, _mx_is_fine
from tests.test_contacts_usage import _search
from tests.test_letters_queue import _build
from tests.test_replies_inbox import NOW, inbound_secret, reply_from, sent
from tests.test_replies_inbox import FakeExtractor as ReplyExtractor

#: Фикстуры соседей: почта у доменов есть (иначе спуск до платной ступени не дойдёт),
#: письмо ушло и ответ на него пришёл.
__all__ = ["_mx_is_fine", "inbound_secret", "reply", "sent"]


class _AdvertiserQueue:
    """Очередь рекламодателей из двух доменов: этап — тот же, что у настоящей."""

    stage = AdvertiserContactRepository.stage

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        return ["adv-one.example.test", "adv-two.example.test"][:limit]

    async def save(self, results: Sequence[LadderResult]) -> int:
        return 0

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return set()


async def _stages(session: AsyncSession, operation: str) -> list[Stage | None]:
    rows = await session.scalars(
        select(UsageRecordModel.stage).where(UsageRecordModel.operation == operation)
    )
    return list(rows.all())


def test_each_queue_names_its_stage() -> None:
    assert ContactRepository.stage is Stage.DONORS
    assert AdvertiserContactRepository.stage is Stage.ADVERTISERS
    assert NamedDomains.stage is None, "перепоиск названных доменов — общий на этапы"


async def test_donor_contact_search_is_stage_one(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _search(session, monkeypatch, Paid(), "one.example.test")

    assert await _stages(session, CONTACTS_OPERATION) == [Stage.DONORS]


async def test_advertiser_contact_search_is_stage_two(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    paid = Paid()
    # Тот же путь, что у доноров (`_search`), но очередь — рекламодателей.
    await _search(session, monkeypatch, paid)
    await search_contacts(session, limit=10, queue=_AdvertiserQueue())

    assert len(paid.calls) == 2
    assert await _stages(session, CONTACTS_OPERATION) == [Stage.ADVERTISERS]


async def test_letter_rewrite_is_written_with_the_stage_of_the_build(
    session: AsyncSession, filled_legal: None
) -> None:
    await make_donor(session, "one.example.test", email="info@one.example.test", dr=50)

    await _build(session)

    assert await _stages(session, "letter_rewrite") == [Stage.DONORS]


async def test_reply_parse_is_written_with_the_stage_of_the_campaign(
    session: AsyncSession, sent: MessageModel
) -> None:
    """Разбор ответа — этапом рассылки, на письмо которой ответили (по кампании)."""
    got = await Inbox(session, now=NOW).accept(reply_from(sent, "Our price is $90."))
    assert got.reply_id is not None
    found = Extracted(price_white=Decimal("90"), currency="USD", confidence=0.99, tokens_spent=77)

    await Parser(session, ReplyExtractor(found), now=NOW).parse(got.reply_id)

    assert await _stages(session, "reply_parse") == [Stage.DONORS]


async def test_agent_draft_is_written_with_the_stage_of_the_conversation(
    session: AsyncSession, reply: ReplyModel
) -> None:
    """Черновик агента — этапом переписки: черновик рекламодателю — расход Этапа 2."""
    await agent_on(session)

    await drafting.draft_answer(session, FakeWriter(), reply.id)

    assert await _stages(session, AGENT_STAGES[Stage.DONORS].usage_operation) == [Stage.DONORS]
