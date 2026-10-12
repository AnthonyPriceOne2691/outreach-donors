"""Свежесть цены — одно правило у таблицы доноров, офферов, главной, обхода и пробы оффера.

ТЗ: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора». До 12.10.2026
у каждого места был свой экземпляр условия, и на краях они расходились: цена без даты
в таблице была «устарела», у обхода — «цены нет», а плитка «С ценой» её считала.
Запросы берут одно выражение (`donors/price_freshness.price_is`); обход, проба
оффера и проверка оффера перед отправкой (`letters/offer_standing`) решают в Питоне —
и сверяются здесь с таблицей на краях: свежая, ровно в срок, устаревшая, без даты,
без цены.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config import filters as filters_cfg
from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import targets
from backend.features.crawl.probe_advertiser import _priced_donor
from backend.features.donors.browse import DonorBrowser
from backend.features.donors.price_freshness import PriceState
from backend.features.donors.probe import ProbeError
from backend.features.donors.table_filters import DonorFilters
from backend.features.letters.offer_standing import _stale_price
from backend.features.letters.recipients import Recipients
from backend.features.ops.overview import overview
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import make_advertiser

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
TTL = timedelta(days=filters_cfg.PRICE_TTL_DAYS)

#: Донор → когда названа цена; `None` у цены — цены нет, `None` у даты — дата не записана.
DONORS: dict[str, tuple[str | None, datetime | None]] = {
    "fresh.example.test": ("150", NOW - timedelta(days=10)),
    "border.example.test": ("150", NOW - TTL),
    "stale.example.test": ("150", NOW - TTL - timedelta(seconds=1)),
    "dateless.example.test": ("150", None),
    "none.example.test": (None, None),
}
STATES = {
    PriceState.FRESH: {"fresh.example.test", "border.example.test"},
    PriceState.STALE: {"stale.example.test"},
    PriceState.NONE: {"dateless.example.test", "none.example.test"},
}


@pytest.fixture
async def priced(session: AsyncSession) -> None:
    for host, (price, at) in DONORS.items():
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        session.add(
            DonorModel(
                domain_id=domain.id,
                status=DonorStatus.SUITABLE,
                review="accepted",
                last_price=None if price is None else Decimal(price),
                last_price_currency="USD",
                last_price_at=at,
            )
        )
        # Оффер «мы дешевле» — рекламодателю, найденному на этом доноре.
        await make_advertiser(session, f"brand-{host}", donor_host=host)
    await session.commit()


async def _table(session: AsyncSession, state: PriceState) -> set[str]:
    page = await DonorBrowser(session).page(DonorFilters(price=state), now=NOW)
    return {row.host for row in page.rows}


async def test_the_table_splits_prices_without_remainder(
    session: AsyncSession, priced: None
) -> None:
    for state, hosts in STATES.items():
        assert await _table(session, state) == hosts
    assert await _table(session, PriceState.PRICED) == STATES[PriceState.FRESH] | {
        "stale.example.test"
    }
    rows = (await DonorBrowser(session).page(DonorFilters(), now=NOW)).rows
    assert {row.host for row in rows if row.price_stale} == STATES[PriceState.STALE]


async def test_the_crawl_says_what_the_table_says(session: AsyncSession, priced: None) -> None:
    chosen = await targets.choose(session, limit=100, now=NOW)

    assert set(chosen.hosts) == STATES[PriceState.FRESH]
    assert set(chosen.stale_price) == STATES[PriceState.STALE]
    assert set(chosen.no_price) == STATES[PriceState.NONE]


async def test_offers_stand_on_the_prices_the_table_calls_fresh(
    session: AsyncSession, priced: None
) -> None:
    funnel = await Recipients(session).advertiser_funnel(now=NOW)

    assert funnel.fresh_price == len(STATES[PriceState.FRESH])


async def test_the_overview_counts_what_the_table_shows(
    session: AsyncSession, priced: None
) -> None:
    found = await overview(session, now=NOW, stages=[Stage.DONORS])

    assert found.donors.priced == len(STATES[PriceState.FRESH]) + len(STATES[PriceState.STALE])
    assert found.donors.priced_fresh == len(STATES[PriceState.FRESH])


@pytest.mark.parametrize(
    ("host", "said"),
    [
        ("stale.example.test", "старше"),
        ("dateless.example.test", "цены нет"),
        ("none.example.test", "цены нет"),
    ],
)
async def test_the_probe_refuses_what_the_table_calls_not_fresh(
    session: AsyncSession, priced: None, host: str, said: str
) -> None:
    with pytest.raises(ProbeError, match=said):
        await _priced_donor(session, host, NOW)


@pytest.mark.parametrize("host", sorted(STATES[PriceState.FRESH]))
async def test_the_probe_takes_what_the_table_calls_fresh(
    session: AsyncSession, priced: None, host: str
) -> None:
    assert await _priced_donor(session, host, NOW) == host


@pytest.mark.parametrize("host", sorted(STATES[PriceState.FRESH]))
async def test_the_offer_leaves_on_what_the_table_calls_fresh(
    session: AsyncSession, priced: None, host: str
) -> None:
    """Проверка оффера при отправке (`letters/offer_standing`) пускает свежую цену."""
    assert await _stale_price(session, host, NOW) is None


@pytest.mark.parametrize("host", sorted(STATES[PriceState.STALE] | STATES[PriceState.NONE]))
async def test_the_offer_waits_on_what_the_table_calls_not_fresh(
    session: AsyncSession, priced: None, host: str
) -> None:
    """…и держит оффер на том, что таблица зовёт «устарела» или «без цены»."""
    assert await _stale_price(session, host, NOW) is not None
