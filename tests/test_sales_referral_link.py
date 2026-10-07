"""«Пишите другому» в диалоге, начатом сборкой продаж (Ф2 поверх 4.6b): лид — по явной связи.

Диалог, который заводит сборка очереди продаж (`sales/queue.py`), строки `contacts` не
имеет: адрес лида живёт у лида. Лид исходного диалога для «пишите другому» находится по
связи `sales_threads` — как и лид передачи (`handoff.lead_of`). Проверяльщик адресов —
выдуманный, MX — заглушкой, как в тестах «пишите другому» (`test_sales_referral.py`).
"""

from __future__ import annotations

import pytest
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import ThreadStatus
from backend.features.core.models.outreach import ThreadModel
from backend.features.letters.sending import Sending
from backend.features.sales import cleaning, referral
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import _lead_of, _queued, _transports

COLLEAGUE = "marketing@acme.example.test"


@pytest.fixture(autouse=True)
def mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен адреса принимает почту: ступень MX проверяется тестами очистки."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def test_referral_in_a_dialog_of_the_queue_finds_its_lead_by_the_link(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «лид исходного диалога — только по адресу контакта»: у диалога сборки
    контакта нет — нового лида не было бы, ответ ждал бы человека."""
    [letter] = await _queued(session, world)
    await Sending(session, _transports(), now=w.NOW).send(letter.id)
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.contact_id is None  # строки `contacts` у диалога продаж нет
    origin = await _lead_of(session, letter)

    referred = await referral.refer(session, thread.id, COLLEAGUE, FixtureVerifier(), now=w.NOW)

    assert referred.lead_id is not None, referred.words
    new = await session.get(SalesLeadModel, referred.lead_id)
    assert new is not None
    assert (new.source, new.status) == (LeadSource.REFERRAL, LeadStatus.READY)
    assert (new.hypothesis_id, new.domain_id) == (origin.hypothesis_id, origin.domain_id)
    assert (new.referred_from_thread_id, new.timezone) == (thread.id, origin.timezone)
    assert thread.status is ThreadStatus.CLOSED
