"""«Перевести в рекламодатели» и поиск им адресов — кнопками, а не консолью.

До 06.10.2026 это была только `outreach advertisers-promote --contacts`.
Проверяется то, ради чего кнопка заведена: числа над ней сходятся
с тем, что она переведёт, решение человека сильнее вердикта, перевод
пишется в журнал, а поиск адресов ставится сам — той же задачей, что
у доноров, но по очереди рекламодателей и под своим ключом.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    CrawlOutcome,
    Stage,
    StopReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.shared.queue import ADVERTISER_CONTACTS_JOB_KEY, CONTACTS_JOB
from backend.workers import jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

DONOR = "donor.example.test"


class FakeQueue:
    """Очередь, которая ничего не выполняет: проверяется, что в неё положили."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.remembered: list[tuple[str, str]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.calls.append((job, args))
        return type("Job", (), {"id": "job-адреса"})()

    def remember(self, job_id: str, *, key: str) -> None:
        self.remembered.append((job_id, key))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    routes = "backend.api.advertisers.routes"
    monkeypatch.setattr(f"{routes}.runs_queue", lambda: fake)
    monkeypatch.setattr(f"{routes}.remember_contacts_job", fake.remember)
    monkeypatch.setattr(f"{routes}.contacts_job_id", lambda **_k: None)
    monkeypatch.setattr(f"{routes}.workers_alive", lambda: 1)
    return fake


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    """Смотреть может, тратить — нет: поиск адресов доходит до платной ступени."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"run": False})
    return await sign_in("зритель@site.com")


@pytest.fixture
async def crawl(session: AsyncSession) -> CrawlRunModel:
    run = CrawlRunModel(
        host=DONOR,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=12,
        articles=10,
    )
    session.add(run)
    await session.flush()
    return run


async def _candidate(
    session: AsyncSession,
    run: CrawlRunModel,
    root: str,
    verdict: Verdict,
    *,
    confirmed: bool | None = None,
    points: int = 5,
) -> CandidateModel:
    row = CandidateModel(
        crawl_run_id=run.id,
        donor_host=DONOR,
        target_root=root,
        points=points,
        verdict=verdict,
        reasons=["пометка рекламы +5"],
        links=1,
        pages=1,
        best_page_url=f"https://{DONOR}/post/{root}",
        best_anchor=root,
        confirmed=confirmed,
    )
    session.add(row)
    await session.flush()
    return row


async def _advertiser(session: AsyncSession, host: str, *, address: str | None = None) -> None:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(AdvertiserModel(domain_id=domain.id, points=5, links=1, best_donor_host=DONOR))
    if address is not None:
        session.add(ContactModel(domain_id=domain.id, email=address, source=ContactSource.PAGE))
    await session.flush()


@pytest.fixture
async def mixed(session: AsyncSession, crawl: CrawlRunModel) -> None:
    """Куплено, «пишем» из спорных, «не пишем» из купленных и просто спорный."""
    await _candidate(session, crawl, "bought.example", Verdict.BOUGHT)
    await _candidate(session, crawl, "yes.example", Verdict.PENDING, confirmed=True, points=3)
    await _candidate(session, crawl, "rejected.example", Verdict.BOUGHT, confirmed=False)
    await _candidate(session, crawl, "undecided.example", Verdict.PENDING, points=2)
    await session.commit()


class TestNumbersAboveTheButton:
    async def test_ready_counts_bought_and_yes_but_not_no(
        self, client: AsyncClient, operator_token: str, mixed: None
    ) -> None:
        """Число над кнопкой — ровно те, кого она переведёт: «не пишем»
        человека сильнее вердикта «куплено», нерешённый спорный не идёт."""
        response = await client.get("/api/advertisers/promotion", headers=bearer(operator_token))

        assert response.status_code == 200, response.text
        assert response.json() == {"ready": 2, "fresh": 2, "advertisers": 0, "with_address": 0}

    async def test_already_promoted_are_not_fresh(
        self, client: AsyncClient, operator_token: str, mixed: None, session: AsyncSession
    ) -> None:
        await _advertiser(session, "bought.example", address="ads@bought.example")
        await session.commit()

        body = (
            await client.get("/api/advertisers/promotion", headers=bearer(operator_token))
        ).json()

        assert body == {"ready": 2, "fresh": 1, "advertisers": 1, "with_address": 1}

    async def test_two_links_to_one_domain_count_once(
        self,
        client: AsyncClient,
        operator_token: str,
        crawl: CrawlRunModel,
        session: AsyncSession,
    ) -> None:
        """Один рекламодатель — одно письмо, сколько бы ссылок на него ни нашлось."""
        await _candidate(session, crawl, "twice.example", Verdict.BOUGHT)
        await _candidate(session, crawl, "twice.example", Verdict.BOUGHT, points=6)
        await session.commit()

        body = (
            await client.get("/api/advertisers/promotion", headers=bearer(operator_token))
        ).json()

        assert body["ready"] == 1


class TestPromote:
    async def test_promotes_and_queues_the_address_search(
        self,
        client: AsyncClient,
        operator_token: str,
        mixed: None,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        response = await client.post("/api/advertisers/promote", headers=bearer(operator_token))

        assert response.status_code == 200, response.text
        body = response.json()
        assert sorted(body["fresh"]) == ["bought.example", "yes.example"]
        assert body["report"]["заведено"] == 2
        assert body["report"]["подтверждено человеком"] == 1
        assert body["pending"] == 2
        assert body["contacts_job_id"] == "job-адреса"
        hosts = (
            await session.execute(
                select(DomainModel.host).join(
                    AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id
                )
            )
        ).scalars()
        assert sorted(hosts) == ["bought.example", "yes.example"]

    async def test_search_goes_by_the_advertisers_queue_under_its_own_key(
        self, client: AsyncClient, operator_token: str, mixed: None, queue: FakeQueue
    ) -> None:
        """Задача та же, что у доноров, — доводы обязаны совпасть с её подписью,
        а номер помнится под своим ключом: иначе экран доноров показал бы
        этот поиск как свой."""
        await client.post("/api/advertisers/promote", headers=bearer(operator_token))

        path, args = queue.calls[0]
        assert path == CONTACTS_JOB
        bound = inspect.signature(jobs.find_contacts).bind(*args)
        assert bound.arguments["stage"] == Stage.ADVERTISERS.value
        assert bound.arguments["limit"] == 2
        assert bound.arguments["donor_id"] is None
        assert queue.remembered == [("job-адреса", ADVERTISER_CONTACTS_JOB_KEY)]

    async def test_nobody_without_address_means_no_search(
        self,
        client: AsyncClient,
        operator_token: str,
        crawl: CrawlRunModel,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """Платная ступень не ставится впустую: всем переведённым есть куда писать."""
        await _candidate(session, crawl, "known.example", Verdict.BOUGHT)
        await _advertiser(session, "known.example", address="ads@known.example")
        await session.commit()

        body = (
            await client.post("/api/advertisers/promote", headers=bearer(operator_token))
        ).json()

        assert body["report"]["обновлено"] == 1
        assert body["pending"] == 0
        assert body["contacts_job_id"] is None
        assert queue.calls == []

    async def test_promotion_is_in_the_journal(
        self,
        client: AsyncClient,
        operator_token: str,
        mixed: None,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """«Откуда у нас этот адресат» спросят — ответ в журнале: кто перевёл и сколько."""
        await client.post("/api/advertisers/promote", headers=bearer(operator_token))

        entry = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.target == "advertisers:promote")
            )
        ).scalar_one()
        assert entry.action is AuditAction.ADVERTISER_REVIEWED
        assert entry.details["действие"] == "перевод в рекламодатели"
        assert entry.details["заведено"] == 2

    async def test_without_run_permission_refused(
        self, client: AsyncClient, viewer_token: str, mixed: None, queue: FakeQueue
    ) -> None:
        promoted = await client.post("/api/advertisers/promote", headers=bearer(viewer_token))
        searched = await client.post(
            "/api/advertisers/contacts", json={"limit": 10}, headers=bearer(viewer_token)
        )

        assert promoted.status_code == 403
        assert searched.status_code == 403
        assert queue.calls == []


class TestAddressSearch:
    async def test_state_counts_advertisers_waiting_for_an_address(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        await _advertiser(session, "waiting.example")
        await _advertiser(session, "known.example", address="ads@known.example")
        await session.commit()

        body = (
            await client.get("/api/advertisers/contacts", headers=bearer(operator_token))
        ).json()

        assert body["pending"] == 1
        assert body["running"] is False
        assert body["workers"] == 1

    async def test_search_is_capped_and_goes_by_advertisers(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        await _advertiser(session, "waiting.example")
        await session.commit()

        response = await client.post(
            "/api/advertisers/contacts", json={"limit": 1000}, headers=bearer(operator_token)
        )

        assert response.json() == {"job_id": "job-адреса", "pending": 1}
        _, args = queue.calls[0]
        bound = inspect.signature(jobs.find_contacts).bind(*args)
        assert bound.arguments["stage"] == Stage.ADVERTISERS.value
        assert bound.arguments["limit"] == 1000
