"""«Куплено» списком и дата статьи у ссылки — то, чего экрану не хватало.

Экран показывал только спорных: «куплено» было числом в шапке, и ложного
среди купленных снять было нечем, кроме консоли. Дата статьи — второе:
свежее размещение живое, статья 2012 года — давно забытое, и писать
рекламодателю «вы покупаете у таких, как мы» по ней — промах.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from backend.features.core.domain import CrawlOutcome, StopReason, UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

DONOR = "donor.example.test"
PAGE = f"https://{DONOR}/post/1"


@pytest.fixture
async def token(
    make_user: Callable[..., Awaitable[UserModel]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def bought(session: AsyncSession) -> CandidateModel:
    run = CrawlRunModel(
        host=DONOR,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=3,
        articles=3,
    )
    session.add(run)
    await session.flush()
    session.add(
        OutLinkModel(
            crawl_run_id=run.id,
            page_url=PAGE,
            url="https://brand.example/offer",
            target_host="brand.example",
            target_root="brand.example",
            anchor="Brand",
            anchor_key="brand",
            page_published=date(2014, 3, 12),
        )
    )
    row = CandidateModel(
        crawl_run_id=run.id,
        donor_host=DONOR,
        target_root="brand.example",
        points=5,
        verdict=Verdict.BOUGHT,
        reasons=["пометка спонсорской ссылки (rel=sponsored) +5"],
        links=1,
        pages=1,
        best_page_url=PAGE,
        best_anchor="Brand",
    )
    session.add(row)
    session.add(
        CandidateModel(
            crawl_run_id=run.id,
            donor_host=DONOR,
            target_root="undated.example",
            points=4,
            verdict=Verdict.BOUGHT,
            reasons=["пометка рекламного материала +4"],
            links=1,
            pages=1,
            best_page_url=f"https://{DONOR}/no-date",
        )
    )
    await session.commit()
    return row


async def test_bought_are_listed_with_the_article_date(
    client: AsyncClient, token: str, bought: CandidateModel
) -> None:
    response = await client.get("/api/advertisers?verdict=bought", headers=bearer(token))

    rows = response.json()["rows"]
    assert [row["target_root"] for row in rows] == ["brand.example", "undated.example"]
    assert rows[0]["best_published"] == "2014-03-12"
    assert rows[1]["best_published"] is None, "даты нет — так и показываем, а не выдумываем"
    assert response.json()["waiting"] == 0, "«ждут человека» — по-прежнему спорные"


async def test_bought_one_can_be_taken_off(
    client: AsyncClient, token: str, bought: CandidateModel
) -> None:
    """«Не пишем» на «куплено» — снять ложного до письма."""
    decided = await client.post(
        f"/api/advertisers/{bought.id}/decide", json={"confirmed": False}, headers=bearer(token)
    )
    rest = await client.get("/api/advertisers?verdict=bought", headers=bearer(token))

    assert decided.json()["confirmed"] is False
    assert [row["target_root"] for row in rest.json()["rows"]] == ["undated.example"]


async def test_other_verdicts_are_numbers_not_lists(client: AsyncClient, token: str) -> None:
    response = await client.get("/api/advertisers?verdict=skipped", headers=bearer(token))

    assert response.status_code == 422
    assert "видны числами в шапке" in response.text
