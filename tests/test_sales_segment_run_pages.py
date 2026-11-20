"""Купленные страницы выдачи прогона в ответе экрана «Сбор базы» — первая купленная (`from_page`).

Стенд 19.11: карточка прогона волны плана «страницы 4–5» писала «50 результатов на ключ», хотя
куплено 20: глубину прогона экран знал, а с какой страницы он покупал — нет. Строка прогона — в
списке прогонов гипотезы и в отчёте прогона — несёт `from_page`; прогон экрана той же гипотезы —
с первой. Ключи и сайты выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.api.sales.segment_schemas import RunRow
from backend.features.core.models.access import UserModel
from backend.features.sales.plan_waves import Batch, Wave
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_plan_waves import KEYS, make_plan, make_run, sheet

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def test_a_wave_run_says_from_which_page_it_bought_in_the_list_and_the_report(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan = await make_plan(session)
    row = await sheet(session, plan)
    second = await make_run(session, row, Batch(Wave(2, 2), "de", KEYS[:3]))
    deepest = await make_run(session, row, Batch(Wave(4, 5), "de", KEYS[:2]))
    manual = await make_run(session, row, Batch(Wave(1, 1), "de", KEYS[:1]), own=False)
    await session.commit()

    home = await client.get(f"/api/sales/segment?hypothesis={row.hypothesis_id}", headers=headers)
    report = await client.get(f"/api/sales/segment/runs/{deepest.id}", headers=headers)

    assert home.status_code == 200, home.text
    listed = {run["id"]: (run["from_page"], run["depth_pages"]) for run in home.json()["runs"]}
    assert listed == {second.id: (2, 2), deepest.id: (4, 5), manual.id: (1, 1)}
    assert report.status_code == 200, report.text
    found = report.json()["run"]
    assert (found["from_page"], found["depth_pages"]) == (4, 5)
    assert "from_page" in RunRow.model_fields
