"""Адрес лида каждой переписки продаж — ответ модуля спискам почты (`sales/lead_addresses.py`).

Строки `contacts` у переписки продаж нет (решение 01.10): адрес живёт у лида, лид — у переписки по
явной связи `sales_threads`. Спискам «Диалогов» и писем модуль отвечает пачкой (`mail.addresses`):
номер переписки → адрес её лида; переписок без лида в ответе нет. Номера уходят параметрами
запроса пачками (`CHUNK`): у Postgres их не больше 32 767. База настоящая, адреса выдуманы.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from backend.features.core.domain import ContactSource, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.sales import lead_addresses, mail
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

JANE = "jane@acme.example.test"
OLGA = "olga@acme.example.test"
MAX = "max@globex.example.test"
#: Адрес сайта у доноров — строка `contacts`, не лид.
EDITOR = "editor@acme.example.test"


async def _campaign(session: AsyncSession, stage: Stage) -> CampaignModel:
    campaign = CampaignModel(name=f"Адреса {stage.value}", stage=stage, status="running")
    session.add(campaign)
    await session.flush()
    return campaign


async def _domain(session: AsyncSession, host: str) -> DomainModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    return domain


async def _thread(
    session: AsyncSession, campaign: CampaignModel, domain: DomainModel, contact_id: int | None
) -> ThreadModel:
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact_id)
    session.add(thread)
    await session.flush()
    return thread


async def _dialog(
    session: AsyncSession, campaign: CampaignModel, domain: DomainModel, email: str
) -> ThreadModel:
    """Переписка продаж, как её заводит сборка очереди: без строки `contacts`, лид — по связи."""
    hypothesis = SalesHypothesisModel(name=f"Гипотеза {email}")
    session.add(hypothesis)
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=email,
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    thread = await _thread(session, campaign, domain, None)
    session.add(lead)
    await session.flush()
    session.add(
        SalesThreadModel(
            thread_id=thread.id, lead_id=lead.id, language="en", chain_version="chain-made-up-7"
        )
    )
    await session.flush()
    return thread


async def test_each_sales_dialog_gets_its_own_lead_and_nothing_else_answers(
    session: AsyncSession,
) -> None:
    """Два лида одной компании — две переписки одного домена: по домену их не различить, адрес —
    по связи переписки. Переписка продаж без лида (её начала не сборка продаж), переписка доноров
    и несуществующий номер в ответе не стоят: адреса им модуль не знает. И только о спрошенных:
    переписка продаж, о которой не спросили, в ответ не попадает."""
    sales = await _campaign(session, Stage.SALES)
    acme = await _domain(session, "acme.example.test")
    globex = await _domain(session, "globex.example.test")
    jane = await _dialog(session, sales, acme, JANE)
    olga = await _dialog(session, sales, acme, OLGA)
    await _dialog(session, sales, globex, MAX)
    unlinked = await _thread(session, sales, globex, None)
    contact = ContactModel(domain_id=acme.id, email=EDITOR, source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()
    donor = await _thread(session, await _campaign(session, Stage.DONORS), acme, contact.id)

    found = await mail.addresses(
        session, sorted([jane.id, olga.id, unlinked.id, donor.id, donor.id + 1000])
    )

    assert found == {jane.id: JANE, olga.id: OLGA}


async def test_numbers_go_in_chunks_and_the_answer_is_whole(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Список «Диалогов» спрашивает обо всех переписках продаж разом: номера — пачками по
    `CHUNK`, по запросу на пачку, а ответ — целиком, из всех пачек."""
    sales = await _campaign(session, Stage.SALES)
    acme = await _domain(session, "acme.example.test")
    threads = [await _dialog(session, sales, acme, email) for email in (JANE, OLGA, MAX)]
    monkeypatch.setattr(lead_addresses, "CHUNK", 2)

    with _queries(session) as asked:
        found = await lead_addresses.by_thread(session, [thread.id for thread in threads])

    assert found == dict(zip([thread.id for thread in threads], [JANE, OLGA, MAX], strict=True))
    assert asked == [2, 1]


async def test_nothing_asked_is_nothing_read(session: AsyncSession) -> None:
    with _queries(session) as asked:
        found = await lead_addresses.by_thread(session, [])

    assert (found, asked) == ({}, [])


@contextmanager
def _queries(session: AsyncSession) -> Iterator[list[int]]:
    """Сколько номеров ушло в каждом запросе к связи переписок — параметров его `IN`."""
    asked: list[int] = []

    def spy(_conn: object, _cursor: object, statement: str, parameters: object, *_: object) -> None:
        if "sales_threads" in statement:
            asked.append(len(parameters))  # type: ignore[arg-type]

    target = session.bind.sync_connection  # type: ignore[union-attr]
    event.listen(target, "before_cursor_execute", spy)
    try:
        yield asked
    finally:
        event.remove(target, "before_cursor_execute", spy)
