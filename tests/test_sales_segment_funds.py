"""Счёт выдачи у сбора базы продаж (`serp/funds`, слово владельца 16.10.2026): на счету DataForSEO
меньше порога или выдача по смете дороже остатка — смета отказывает словами «пополните счёт»,
«Запустить» — 409 теми же словами и без строки прогона, задача — постоянным отказом до покупки.
Остаток не узнать — проверки нет: на этом стоят остальные тесты сбора (общая подмена
`conftest._no_real_serp_balance`).

Остаток у сметы и запуска — подменой `funds.provider_balance`, у задачи — счётом поддельного
источника выдачи. Ключи — трёх ниш и письменностей; числа выдуманы и не круглы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from backend.config import serp as serp_cfg
from backend.features.core.models.ops import UsageRecordModel
from backend.features.runs.failures import is_permanent
from backend.features.sales import segment_jobs
from backend.features.sales.segment_estimate import estimate
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_serp import collect
from backend.features.serp import funds
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_api import headers, jobs, start
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_run import KEYWORD, Serp, hypothesis, make_run

__all__ = ["_settings", "headers", "jobs"]  # настройки сметы и вход продавца — как у их тестов

#: Велосипеды кириллицей, стоматологи латиницей, кафе — японским письмом.
KEYWORDS = (KEYWORD, "zahnarzt berlin mitte", "東京 カフェ 予約")
FLOOR_WORDS = "меньше 5,00\u00a0$: пополните счёт DataForSEO. До пополнения прогоны не запускаются."


def answers(left: float | None) -> Callable[[object], Awaitable[float | None]]:
    """Остаток счёта выдачи для сметы и запуска — как ответил бы провайдер."""

    async def balance(_ahrefs: object) -> float | None:
        return left

    return balance


@dataclass
class Funded(Serp):
    """Выдача-подделка со счётом: остаток — как ответил бы провайдер (`funds.balance_of`)."""

    left: float = 0.0

    async def balance(self) -> float:
        return self.left


# --- смета и «Запустить» --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "price", "depth", "said"),
    [
        (3.17, 0.0007, 1, f"На счету DataForSEO 3,17\u00a0$ — {FLOOR_WORDS}"),
        (
            5.43,
            0.21,
            10,
            "Выдача этого прогона по смете ≈ 6,30\u00a0$, а на счету DataForSEO 5,43\u00a0$: пополните "
            "счёт DataForSEO.",
        ),
    ],
    ids=["below-the-floor", "dearer-than-the-balance"],
)
async def test_the_estimate_refuses_in_words_while_the_serp_account_is_short(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    left: float,
    price: float,
    depth: int,
    said: str,
) -> None:
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", price)
    monkeypatch.setattr(funds, "provider_balance", answers(left))
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, keywords="\n".join(KEYWORDS), depth_pages=depth))

    assert found.serp_usd == pytest.approx(len(KEYWORDS) * depth * price)
    assert found.refusal == said


@pytest.mark.parametrize("left", [None, 61.9], ids=["unknown", "enough"])
async def test_an_unknown_or_sufficient_balance_does_not_refuse(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, left: float | None
) -> None:
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", 0.21)
    monkeypatch.setattr(funds, "provider_balance", answers(left))
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, keywords="\n".join(KEYWORDS), depth_pages=10))

    assert found.refusal is None


async def test_start_is_refused_in_the_words_of_the_estimate_and_leaves_no_run(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Счёт ушёл между сметой и нажатием — запуск пересчитывает смету на сервере."""
    monkeypatch.setattr(funds, "provider_balance", answers(2.75))
    owner = await hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Прогон не запущен: На счету DataForSEO 2,75\u00a0$ — {FLOOR_WORDS}"
    )
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0
    assert jobs.enqueued == []


# --- задача перед покупкой выдачи -----------------------------------------------------------------


async def test_the_purchase_is_refused_before_buying_and_the_run_says_why(
    session: AsyncSession,
) -> None:
    """Счёт мог уйти между нажатием и задачей: отказ — до покупки, без повтора задачи."""
    run = await make_run(session, await hypothesis(session), keywords=KEYWORDS)
    found = Funded({}, left=1.37)

    with pytest.raises(funds.FundsTooLowError) as refused:
        await collect(session, run, found)

    assert found.calls == []
    assert await session.scalar(select(func.count()).select_from(UsageRecordModel)) == 0
    assert is_permanent(refused.value)
    # В строку прогона — слова отказа как есть: в них нет имени настройки.
    assert (
        segment_jobs.stop_words(refused.value) == f"На счету DataForSEO 1,37\u00a0$ — {FLOOR_WORDS}"
    )


async def test_a_purchase_dearer_than_the_balance_is_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serp_cfg, "MODE", "standard")
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", 2.9)
    run = await make_run(session, await hypothesis(session), keywords=KEYWORDS)
    found = Funded({}, left=6.2)

    with pytest.raises(funds.FundsTooLowError, match=r"по смете ≈ 8,70\u00a0\$"):
        await collect(session, run, found)

    assert found.calls == []


@pytest.mark.parametrize(
    ("paid", "left"),
    [(KEYWORDS[:2], 6.2), (KEYWORDS, 0.4)],
    ids=["one-left-to-buy", "all-paid"],
)
async def test_keywords_paid_by_an_earlier_try_do_not_count_against_the_account(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, paid: tuple[str, ...], left: float
) -> None:
    """Повтор после сбоя опроса: оплаченное забирается по номерам задач. Один ключ к покупке —
    2,90 $ при остатке 6,20 $; оплачены все — счёт не спрашивают, хоть он и ниже порога."""
    monkeypatch.setattr(serp_cfg, "MODE", "standard")
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", 2.9)
    run = await make_run(session, await hypothesis(session), keywords=KEYWORDS)
    run.report = {"serp": {"tasks": {keyword: f"task-{n}" for n, keyword in enumerate(paid)}}}
    found = Funded({}, left=left)

    await collect(session, run, found)

    assert len(found.calls) == 1
