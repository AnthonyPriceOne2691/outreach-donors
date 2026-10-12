"""Свежесть цены донора — одно правило на все места, где о ней спрашивают.

ТЗ: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора». О ней
спрашивают таблица доноров и её выгрузка, карточка, офферы Этапа 2 и бизнесам ниши
(`letters/recipients._fresh_price`, `niche_recipients._example_host`), плитка «С ценой»
главной (`ops/overview`), обход доноров (`crawl/targets.choose`), проба оффера
(`crawl/probe_advertiser`) и проверка оффера перед отправкой (`letters/offer_standing`).
До 12.10.2026 у каждого был свой экземпляр условия, и на краях они расходились: цена
без даты в таблице была «устарела», а у обхода — «цены нет». Здесь — одно выражение
для запросов; у обхода, пробы и проверки перед отправкой правило то же в Питоне,
и сверку держит тест (`tests/test_price_freshness.py`).

**Цена без даты — «без цены».** Срок не с чего отсчитать, и перезапрашивать по ней
нечего; так её зовут обход, проба оффера и перезапрос. Пишется цена всегда с датой
(`price.put_price`), и такой строки в базе быть не должно — правило нужно для того,
чтобы на краю все отвечали одно.

**Свежая — названа не раньше срока назад** (`>=`): ровно в день срока цена ещё
свежая, на следующий миг — устарела. Срок — от того, когда донор цену назвал.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import assert_never

from sqlalchemy import ColumnElement, and_, or_

from backend.config import filters as filters_cfg
from backend.features.core.models.donor import DonorModel


class PriceState(StrEnum):
    """Цена донора: есть ли и в сроке ли. «С ценой» — любая с датой, как у плитки
    «С ценой» главной; «без цены» — нет её, нет даты или её снял ответ донора."""

    PRICED = "priced"
    FRESH = "fresh"
    STALE = "stale"
    NONE = "none"


def price_border(now: datetime, ttl_days: int | None = None) -> datetime:
    """Названная раньше этой минуты цена устарела."""
    days = filters_cfg.PRICE_TTL_DAYS if ttl_days is None else ttl_days
    return now - timedelta(days=days)


def price_is(
    state: PriceState, now: datetime, *, ttl_days: int | None = None
) -> ColumnElement[bool]:
    """Условие одного состояния цены — для таблицы, офферов, главной. Четыре условия:
    «с ценой» делится на «свежая» и «устарела» без остатка, «без цены» — всё прочее."""
    at = DonorModel.last_price_at
    priced = and_(DonorModel.last_price.is_not(None), at.is_not(None))
    match state:
        case PriceState.PRICED:
            return priced
        case PriceState.FRESH:
            return and_(priced, at >= price_border(now, ttl_days))
        case PriceState.STALE:
            return and_(priced, at < price_border(now, ttl_days))
        case PriceState.NONE:
            return or_(DonorModel.last_price.is_(None), at.is_(None))
        case unknown:
            assert_never(unknown)


def fresh_until(donor: DonorModel) -> datetime | None:
    """До какого дня цена свежая — для карточки и выгрузки. Без цены или даты — пусто."""
    if donor.last_price is None or donor.last_price_at is None:
        return None
    return donor.last_price_at + timedelta(days=filters_cfg.PRICE_TTL_DAYS)
