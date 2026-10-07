"""Обходы Этапа 2 через экран: кого можно обойти, запуск, ход.

Очередь подменена: живую постановку забрали бы старые воркеры на машине
разработчика. Проверяется то, ради чего экран: обходят только доноров
со свежей ценой — и сервер говорит «почему нет», а не молчит; второй
обход того же донора не заводится; каждый запуск — в журнале.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.api.crawls import routes
from backend.features.core.domain import AuditAction, CrawlStatus, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.manual_price import enter_host, manual_price
from backend.features.replies.repository import ReplyRepository
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

FRESH = "fresh.example.test"
STALE = "stale.example.test"


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str | None]]:
    put: list[tuple[int, str | None]] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        put.append((run_id, job_id))
        return job_id or "x"

    monkeypatch.setattr(routes, "enqueue_crawl", enqueue)
    monkeypatch.setattr(routes, "workers_alive", lambda **_: 4)
    return put


@pytest.fixture
async def donors(session: AsyncSession) -> None:
    """Донор со свежей ценой и донор с протухшей."""
    now = datetime.now(UTC)
    for host, priced_at in ((FRESH, now - timedelta(days=3)), (STALE, now - timedelta(days=400))):
        domain = await make_donor(session, host)
        donor = (
            await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
        ).scalar_one()
        donor.last_price, donor.last_price_at = Decimal("150.00"), priced_at
    await session.commit()


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("смотрит@site.com", permissions={"run": False})
    return await sign_in("смотрит@site.com")


async def test_who_may_see_and_start(client: AsyncClient, viewer: str, donors: None) -> None:
    assert (await client.get("/api/crawls/targets")).status_code == 401
    assert (await client.get("/api/crawls", headers=bearer(viewer))).status_code == 200
    refused = await client.post("/api/crawls", json={"hosts": [FRESH]}, headers=bearer(viewer))
    assert refused.status_code == 403


async def test_targets_are_fresh_priced_donors_with_their_last_crawl(
    client: AsyncClient, operator: str, donors: None, session: AsyncSession
) -> None:
    session.add(
        CrawlRunModel(host=FRESH, status=CrawlStatus.RUNNING, pages_opened=340, articles=300)
    )
    await session.commit()

    body = (await client.get("/api/crawls/targets", headers=bearer(operator))).json()

    assert [donor["host"] for donor in body["donors"]] == [FRESH]
    donor = body["donors"][0]
    assert donor["price"] == 150.0
    assert donor["crawl"]["status"] == "running"
    assert (donor["crawl"]["pages"], donor["crawl"]["max_pages"]) == (340, body["max_pages"])
    assert (body["stale_price"], body["no_price"], body["supplier"]) == (1, 0, 0)
    assert body["workers"] == 4


async def test_a_row_says_the_currency_and_where_the_price_came_from(
    client: AsyncClient, operator: str, donors: None, session: AsyncSession
) -> None:
    """Конвертации нет, и «150» без валюты читалось долларами, даже названное в евро;
    ручная цена — с пометкой: видно, на чём держится «мы дешевле». У цены, записанной
    до 07.10.2026, источника нет — тогда её давал только ответ."""
    replied = await make_donor(session, "replied.example.test")
    await ReplyRepository(session).store_price(
        domain_id=replied.id, price=Decimal("120.00"), currency="EUR", offers=None
    )
    await enter_host(
        session,
        "example.com",
        manual_price("90", "USD", "прайс агентства", by="anna@ours.example.test"),
    )
    await session.commit()

    body = (await client.get("/api/crawls/targets", headers=bearer(operator))).json()

    rows = {row["host"]: (row["price"], row["currency"], row["source"]) for row in body["donors"]}
    assert rows == {
        FRESH: (150.0, None, None),
        "replied.example.test": (120.0, "EUR", "reply"),
        "example.com": (90.0, "USD", "manual"),
    }


async def test_start_queues_fresh_and_names_the_rest(
    client: AsyncClient,
    operator: str,
    donors: None,
    session: AsyncSession,
    queue: list[tuple[int, str | None]],
) -> None:
    response = await client.post(
        "/api/crawls",
        json={"hosts": [f"www.{FRESH}", STALE, "nobody.example.test"]},
        headers=bearer(operator),
    )

    body = response.json()
    assert response.status_code == 200, body
    assert list(body["queued"]) == [FRESH]
    assert body["refused"][STALE].startswith("цена старше")
    assert body["refused"]["nobody.example.test"].startswith("не принятый донор")
    run_id = body["queued"][FRESH]
    assert queue == [(run_id, queue[0][1])]
    assert queue[0][1] is not None
    assert queue[0][1].startswith(f"crawl-{run_id}-")
    journal = (await session.execute(select(AuditLogModel))).scalars().all()
    started = [row for row in journal if row.action is AuditAction.RUN_STARTED]
    assert [(row.target, row.details["донор"]) for row in started] == [(f"crawl:{run_id}", FRESH)]

    again = await client.post("/api/crawls", json={"hosts": [FRESH]}, headers=bearer(operator))
    assert again.json()["busy"] == [FRESH]

    listed = (await client.get("/api/crawls", headers=bearer(operator))).json()
    assert listed["active"] == 1
    assert [(row["host"], row["status"]) for row in listed["rows"]] == [(FRESH, "queued")]
    assert listed["rows"][0]["requested_by"] == "оператор@site.com"


async def test_too_many_at_once_is_refused_in_words(client: AsyncClient, operator: str) -> None:
    hosts = [f"d{n}.example.test" for n in range(51)]

    response = await client.post("/api/crawls", json={"hosts": hosts}, headers=bearer(operator))

    assert response.status_code == 422
    assert "не больше 50 доноров" in response.text


async def test_nothing_to_crawl_says_what_to_do_first(client: AsyncClient, operator: str) -> None:
    body = (await client.get("/api/crawls/targets", headers=bearer(operator))).json()

    assert body["donors"] == []
    assert any("сначала прогон Этапа 1" in note for note in body["notes"])
