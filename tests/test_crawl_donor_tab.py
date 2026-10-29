"""Вкладка «Обход доноров» (29.10.2026): доноры страницами, фильтры и счётчики на сервере.

Слово Anthony: доноров будут тысячи, панель вываливала их простынёй. Проверяется то, ради чего
вкладка: в выборке — доноры отбора обхода с любой ценой, статус — по последнему обходу,
счётчик каждого фильтра — без своего условия, «Обойти всех по фильтру» ставит ровно тех, кого
можно обойти, и не больше, чем человек видел; многих — фоном.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.api.crawls import routes
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import donor_page
from backend.shared.crawl_marks import Held
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)


#: Почему сторож нагрузки держит копии — словами его отметки.
HELD_WHY = "свободно 1,2 ядра, а новому обходу нужно 1,75"


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str | None]]:
    put: list[tuple[int, str | None]] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        put.append((run_id, job_id))
        return job_id or "x"

    monkeypatch.setattr(routes, "enqueue_crawl", enqueue)
    monkeypatch.setattr(routes, "workers_alive", lambda **_: 8)
    monkeypatch.setattr(routes, "job_positions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(routes, "crawlers_held", lambda **_: Held(2, HELD_WHY))
    return put


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def donor(
    session: AsyncSession,
    host: str,
    *,
    price: str | None = "150.00",
    days: int = 3,
    source: str | None = "reply",
    **made: object,
) -> None:
    domain = await make_donor(session, host, **made)  # type: ignore[arg-type]
    row = (
        await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
    ).scalar_one()
    if price is not None:
        row.last_price, row.last_price_at = Decimal(price), NOW - timedelta(days=days)
        row.last_price_source, row.last_price_currency = source, "USD"


def run(
    host: str, status: CrawlStatus, outcome: CrawlOutcome | None = None, **kw: object
) -> CrawlRunModel:
    return CrawlRunModel(host=host, status=status, outcome=outcome, **kw)  # type: ignore[arg-type]


@pytest.fixture
async def tab(session: AsyncSession) -> None:
    """Семь доноров во всех состояниях, поставщик и отклонённый — их во вкладке нет."""
    await donor(session, "never.example.test", days=1)
    await donor(session, "done.example.test", days=2, source="manual")
    await donor(session, "running.example.test", days=3)
    await donor(session, "stale.example.test", days=400)
    await donor(session, "noprice.example.test", price=None)
    await donor(session, "refused.example.test", days=4)
    await donor(session, "partial.example.test", days=5)
    await donor(session, "supplier.example.test", days=1)
    await donor(session, "rejected.example.test", days=1, review="rejected")
    session.add_all(
        [
            run("done.example.test", CrawlStatus.DONE, CrawlOutcome.OK, stats={"links_found": 40}),
            run("running.example.test", CrawlStatus.RUNNING, pages_opened=340),
            run("stale.example.test", CrawlStatus.QUEUED, job_id="crawl-q"),
            run("refused.example.test", CrawlStatus.DONE, CrawlOutcome.BLOCKED),
            run("partial.example.test", CrawlStatus.DONE, CrawlOutcome.OK),
            run(
                "partial.example.test",
                CrawlStatus.DONE,
                CrawlOutcome.PARTIAL,
                stats={"links_found": 7},
            ),
            SupplierDonorModel(host="supplier.example.test", note="партнёр"),
        ]
    )
    await session.commit()


def hosts(body: dict[str, object]) -> list[str]:
    return [row["host"] for row in body["rows"]]  # type: ignore[index, union-attr]


async def test_the_tab_lists_every_crawl_donor_with_status_and_price_counts(
    client: AsyncClient, operator: str, tab: None
) -> None:
    body = (await client.get("/api/crawls/donors", headers=bearer(operator))).json()

    assert body["total"] == 7
    assert hosts(body) == [
        "never.example.test",
        "done.example.test",
        "running.example.test",
        "refused.example.test",
        "partial.example.test",
        "stale.example.test",
        "noprice.example.test",
    ]
    assert body["counts"]["status"] == {
        "never": 2,
        "queued": 1,
        "running": 1,
        "done": 1,
        "partial": 1,
        "refused": 1,
        "stopped": 0,
    }
    assert body["counts"]["price"] == {"fresh": 5, "stale": 1, "none": 1}
    # Обойти можно со свежей ценой и без идущего или ждущего обхода: всех, кроме трёх.
    assert body["startable"] == 4
    assert (body["workers"], body["page"], body["limit"]) == (8, 1, donor_page.PAGE_SIZE)
    # Сторож нагрузки держит копии — вкладка говорит, сколько и почему, как панель до неё.
    assert (body["held"], body["held_reason"]) == (2, HELD_WHY)
    [partial] = [row for row in body["rows"] if row["host"] == "partial.example.test"]
    assert (partial["crawl"]["outcome"], partial["crawl"]["links"]) == ("partial", 7)


async def test_each_count_ignores_only_its_own_condition(
    client: AsyncClient, operator: str, tab: None
) -> None:
    body = (await client.get("/api/crawls/donors?status=refused", headers=bearer(operator))).json()

    assert hosts(body) == ["refused.example.test"]
    assert body["total"] == 1
    assert body["counts"]["status"]["never"] == 2
    assert body["counts"]["price"] == {"fresh": 1, "stale": 0, "none": 0}
    assert body["startable"] == 1


async def test_price_source_search_sort_and_pages(
    client: AsyncClient, operator: str, tab: None
) -> None:
    async def asked(query: str) -> dict[str, object]:
        reply = await client.get(f"/api/crawls/donors?{query}", headers=bearer(operator))
        assert reply.status_code == 200, reply.text
        return reply.json()  # type: ignore[no-any-return]

    assert hosts(await asked("price=stale")) == ["stale.example.test"]
    assert hosts(await asked("source=manual")) == ["done.example.test"]
    assert hosts(await asked("search=REFU")) == ["refused.example.test"]
    assert hosts(await asked("search=%25")) == []
    assert hosts(await asked("sort=host"))[:2] == ["done.example.test", "never.example.test"]
    assert hosts(await asked("sort=links"))[:2] == ["done.example.test", "partial.example.test"]
    crawled = hosts(await asked("sort=crawled"))
    assert crawled[:2] == ["never.example.test", "noprice.example.test"]
    second = await asked("size=25&page=2")
    assert (hosts(second), second["total"]) == ([], 7)
    refused = await client.get("/api/crawls/donors?size=101", headers=bearer(operator))
    assert refused.status_code == 422


async def test_crawl_all_by_filter_queues_only_who_may_be_crawled(
    client: AsyncClient,
    operator: str,
    tab: None,
    session: AsyncSession,
    queue: list[tuple[int, str | None]],
) -> None:
    reply = await client.post(
        "/api/crawls/donors/crawl",
        json={"price": "fresh", "expected": 10},
        headers=bearer(operator),
    )

    body = reply.json()
    assert (body["asked"], body["queued"], body["deferred"]) == (4, 4, False)
    # До полусотни — прямо в очередь обходов, как «Обойти отмеченных», а не в очередь загрузки.
    assert body["backlog"] is None
    assert (await session.scalars(select(CrawlBacklogModel.host))).all() == []
    started = (
        (
            await session.execute(
                select(CrawlRunModel.host).where(CrawlRunModel.status == CrawlStatus.QUEUED)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(started) == [
        "done.example.test",
        "never.example.test",
        "partial.example.test",
        "refused.example.test",
        "stale.example.test",
    ]
    assert len(queue) == 4
    audited = await session.execute(
        select(func.count())
        .select_from(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert audited.scalar_one() == 4


async def test_no_more_than_the_button_said(
    client: AsyncClient, operator: str, tab: None, queue: list[tuple[int, str | None]]
) -> None:
    body = (
        await client.post(
            "/api/crawls/donors/crawl", json={"expected": 1}, headers=bearer(operator)
        )
    ).json()

    assert (body["asked"], body["queued"]) == (1, 1)
    assert len(queue) == 1


async def test_many_go_to_the_import_queue_not_the_crawl_queue(
    client: AsyncClient,
    operator: str,
    tab: None,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    queue: list[tuple[int, str | None]],
) -> None:
    """Больше, чем ставится сразу, — в очередь загрузки: тысячи разом в очереди обходов заперли
    бы за собой каждый обход, поставленный кнопкой после. Встали — больше не «можно обойти»."""
    monkeypatch.setattr(routes, "MAX_HOSTS", 2)

    body = (
        await client.post(
            "/api/crawls/donors/crawl", json={"expected": 50}, headers=bearer(operator)
        )
    ).json()
    after = (await client.get("/api/crawls/donors", headers=bearer(operator))).json()

    assert (body["asked"], body["deferred"], body["backlog"], body["queued"]) == (4, True, 4, None)
    assert queue == []
    waiting = (await session.scalars(select(CrawlBacklogModel.host))).all()
    assert sorted(waiting) == [
        "done.example.test",
        "never.example.test",
        "partial.example.test",
        "refused.example.test",
    ]
    assert after["startable"] == 0
    record = (
        await session.execute(select(AuditLogModel).where(AuditLogModel.target == "crawl_backlog"))
    ).scalar_one()
    assert record.details["доноров"] == 4


async def test_drop_the_import_queue_drops_the_filtered_too(
    client: AsyncClient,
    operator: str,
    tab: None,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    queue: list[tuple[int, str | None]],
) -> None:
    """«Снять очередь» снимает и доноров «Обойти всех по фильтру»: очередь загрузки — одна, и
    снятые снова «можно обойти»."""
    monkeypatch.setattr(routes, "MAX_HOSTS", 2)
    await client.post("/api/crawls/donors/crawl", json={"expected": 50}, headers=bearer(operator))

    dropped = await client.delete("/api/donors/import/backlog", headers=bearer(operator))
    after = (await client.get("/api/crawls/donors", headers=bearer(operator))).json()

    assert dropped.status_code == 200
    assert (await session.scalars(select(CrawlBacklogModel.host))).all() == []
    assert after["startable"] == 4
    assert queue == []


async def test_summary_for_the_review_view(client: AsyncClient, operator: str, tab: None) -> None:
    body = (await client.get("/api/crawls/summary", headers=bearer(operator))).json()

    assert body["counts"]["status"]["never"] == 2
    assert body["counts"]["price"]["stale"] == 1
    assert (body["startable"], body["active"], body["workers"]) == (4, 2, 8)


async def test_recent_crawls_turn_pages_and_count_all(
    client: AsyncClient, operator: str, tab: None
) -> None:
    first = (await client.get("/api/crawls?limit=4", headers=bearer(operator))).json()
    second = (await client.get("/api/crawls?limit=4&page=2", headers=bearer(operator))).json()

    assert (len(first["rows"]), len(second["rows"]), first["total"]) == (4, 2, 6)


async def test_typical_crawl_time_is_the_median_of_real_crawls(session: AsyncSession) -> None:
    assert await donor_page.typical_seconds(session) == donor_page.TYPICAL_FALLBACK_SEC
    session.add_all(
        [
            run(
                "a.example.test",
                CrawlStatus.DONE,
                CrawlOutcome.OK,
                pages_opened=1000,
                stats={"elapsed_sec": 1200},
            ),
            run(
                "b.example.test",
                CrawlStatus.DONE,
                CrawlOutcome.OK,
                pages_opened=900,
                stats={"elapsed_sec": 1800},
            ),
            run(
                "c.example.test",
                CrawlStatus.DONE,
                CrawlOutcome.OK,
                pages_opened=800,
                stats={"elapsed_sec": 1500},
            ),
            run(
                "d.example.test",
                CrawlStatus.DONE,
                CrawlOutcome.BLOCKED,
                pages_opened=0,
                stats={"elapsed_sec": 5},
            ),
        ]
    )
    await session.commit()

    assert await donor_page.typical_seconds(session) == 1500.0
