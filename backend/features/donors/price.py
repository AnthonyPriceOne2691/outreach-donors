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

**Последнее слово донора побеждает** (решение Anthony 11.10.2026). «Не продаём»
снимает цену — с ней домен уходит из офферов Этапа 2: они стоят на свежей цене
донора (`letters/recipients._fresh_price`, `niche_recipients._example_host`).
«Бесплатно» ставит вместо цены отметку — ответ донора на домене
(`domains.seller_answer`). Последнее — по тому, когда донор это сказал,
а не когда мы это разобрали: у ответа — когда он пришёл, у цены руками — когда её
вписали; это время и ложится в `last_price_at` и `seller_answer_at`. Слово старше
уже лежащего ничего не меняет — у всех путей записи одинаково: разбор ответа,
подтверждение человеком, «не продаёт» и цена руками.

До 11.10.2026 ответ без цены цену не трогал, а последним было последнее
записанное: старый ответ с ценой, подтверждённый человеком позже, возвращал
«продаёт» поверх «не продаём».
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from backend.features.core.domain import PriceSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.replies.extract import WITHOUT_PRICE


def said_later(donor: DonorModel, domain: DomainModel | None, at: datetime) -> bool:
    """Лежит ли слово новее `at`: цена — или «не продаём», «бесплатно» на домене.
    Тогда цене, названной в `at`, в карточку не ложиться."""
    if donor.last_price is not None and _after(donor.last_price_at, at):
        return True
    return (
        domain is not None
        and domain.seller_answer in WITHOUT_PRICE
        and _after(domain.seller_answer_at, at)
    )


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

    `at` — когда цену назвали. Слово новее `at` проверяет вызывающий (`said_later`):
    ответ и цена руками отвечают на него по-разному — цена из ответа молча
    не ложится, ручной — отказ словами.
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


def put_answer(
    domain: DomainModel,
    donor: DonorModel | None,
    *,
    answer: str,
    reply_id: int | None,
    at: datetime,
) -> bool:
    """Ответ донора — на домен; «не продаём» и «бесплатно» снимают цену, названную
    не позже `at`. `False` — на домене ответ новее, этот не лёг.

    Цена снимается и тогда, когда сам ответ не лёг: «150 $», «не продаём»,
    «продаём, цену пришлю» — цены нет, хоть последним и было «продаём».
    """
    if answer in WITHOUT_PRICE and donor is not None:
        _drop_price(donor, at)
    if _after(domain.seller_answer_at, at):
        return False
    domain.seller_answer = answer
    domain.seller_answer_at = at
    domain.seller_answer_reply_id = reply_id
    return True


def _drop_price(donor: DonorModel, at: datetime) -> None:
    """Снять цену, названную не позже `at`, — всеми полями, как она пишется."""
    if donor.last_price is None or _after(donor.last_price_at, at):
        return
    donor.last_price = None
    donor.last_price_currency = None
    donor.last_price_at = None
    donor.last_offers = None
    donor.last_price_source = None
    donor.last_price_note = None
    donor.last_price_by = None


def _after(stored: datetime | None, at: datetime) -> bool:
    """Записанное позже `at`. Без отметки — не позже: сравнивать не с чем."""
    return stored is not None and stored > at
