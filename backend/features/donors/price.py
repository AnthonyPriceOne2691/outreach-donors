"""Цена донора — одна запись на оба пути: из ответа и руками.

До 07.10.2026 цену в карточку клал только разбор ответа на письмо Этапа 1
(`replies/repository.store_price`), и поля писались прямо там. Цена, которую
человек знает сам (`manual_price.py`), ложится в те же поля: обход Этапа 2
(`crawl/targets.choose`), скоринг, перевод в рекламодатели и сборка офферов
читают `last_price*` и про источник не знают.

**Пишутся все поля разом, и источник — всегда.** Последняя цена обязана
говорить, откуда она: ручная заметка рядом с ценой из ответа или список цен
чужого ответа рядом с ручной ценой врали бы. Поэтому поле, которого у этого
источника нет, затирается пустым, а не остаётся от прежней цены.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from backend.features.core.domain import PriceSource
from backend.features.core.models.donor import DonorModel


def put_price(
    donor: DonorModel,
    *,
    amount: Decimal,
    currency: str | None,
    at: datetime,
    source: PriceSource,
    offers: list[dict[str, Any]] | None = None,
    note: str | None = None,
    by: str | None = None,
) -> None:
    """Записать последнюю цену донора вместе с тем, откуда она.

    `offers` — все цены того же ответа (у ручной цены ответа нет — пусто);
    `note` и `by` — заметка «откуда цена» и кто её указал (у цены из ответа
    источник — сам ответ, и они пусты).
    """
    donor.last_price = amount
    donor.last_price_currency = currency
    donor.last_price_at = at
    donor.last_offers = offers
    donor.last_price_source = source.value
    donor.last_price_note = note
    donor.last_price_by = by
