"""Записка каскада «чего не хватило» — в ответе экрана обходов.

До 12.10.2026 «отказали подряд все прокси», «сервис рендеринга отключился» и «потолок
Этапа 2 выбран» лежали в записи обхода (`crawl_runs.degradation`), но не доходили ни до
одного экрана: обход выглядел обычным «частично». Теперь записка едет в строке обхода.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.api.crawls import routes
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlRunModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

NOTE = {
    "proxy": "отказали подряд все прокси (2): последний №2 (gate.example.test:8888) — код 403",
    "render": "у Этапа 2 не задан свой потолок — прокси и рендеринг не зовутся",
}


@pytest.fixture(autouse=True)
def no_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Число обходчиков спрашивается у очереди — здесь не о нём."""

    async def none() -> None:
        return None

    monkeypatch.setattr(routes, "_workers", none)


async def test_the_crawl_row_carries_what_the_cascade_lacked(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Awaitable[UserModel]],
    sign_in: Callable[..., Awaitable[str]],
) -> None:
    session.add_all(
        [
            CrawlRunModel(
                host="lacking.example.test",
                status=CrawlStatus.DONE,
                outcome=CrawlOutcome.PARTIAL,
                stop_reason=StopReason.UNHEALTHY,
                pages_opened=120,
                articles=90,
                degradation=NOTE,
            ),
            CrawlRunModel(
                host="fine.example.test",
                status=CrawlStatus.DONE,
                outcome=CrawlOutcome.OK,
                stop_reason=StopReason.EXHAUSTED,
                pages_opened=40,
                articles=30,
            ),
        ]
    )
    await session.commit()
    await make_user("смотрит@site.com", permissions={"run": False})
    headers = bearer(await sign_in("смотрит@site.com"))

    body = (await client.get("/api/crawls", headers=headers)).json()

    rows = {row["host"]: row for row in body["rows"]}
    assert rows["lacking.example.test"]["degradation"] == NOTE
    assert rows["fine.example.test"]["degradation"] == {}
