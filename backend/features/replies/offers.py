"""Цены списком: каждая цена, которую донор назвал в ответе.

В карточку донора ложится одна цена — гостевого поста, белая или серая, —
а донор называет и другие: вставку ссылки в готовую статью, ссылку
на главной, размещение помесячно, свою цену для казино или крипты. До
06.10.2026 модель упоминала их в заметке, а заметка не хранится, и цены
пропадали. Решение Anthony 06.10.2026: хранить список всех названных цен
и показывать его. Главная цена при этом остаётся там же и тем же
(`extract.py`): список её не заменяет, а дополняет.

**Продукт и ниша — словами донора**, в нижнем регистре: это цитата, а не наш
справочник. Сводить «guest post» и «sponsored article» к одному продукту —
работа, которой никто не заказывал, и ошибка в ней выглядела бы ценой,
которой донор не называл.

**Негодный пункт выбрасывается один, а не список целиком.** Не число,
не больше нуля, неправдоподобная сумма, срок, которого мы не знаем, — этот
пункт не цена, а соседние остаются. Число, которого в письме нет, — выдумка:
такие пункты снимает проверка разбора (`extract.temper`), и разбор уходит
человеку.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.features.replies.money import IMPLAUSIBLE_PRICE, as_price, normalize_currency

logger = logging.getLogger(__name__)

TOPIC = "разбор ответа"

#: Сколько цен держит список. Больше — это прайс целиком: его место
#: во вложении, которое человек открывает сам, а не в строках карточки.
MAX_OFFERS = 10

#: Название продукта или ниши — не длиннее шести слов (так просит промпт):
#: длиннее — уже пересказ письма, а не название.
MAX_WORDS = 6
MAX_CHARS = 64

#: Срок цены. Промпт называет два значения; модель изредка отвечает словом
#: рядом, и оно узнаётся. Срок, которого здесь нет, не угадывается: «500 $
#: в неделю» под видом разовой цены — неверное число.
PERIODS: dict[str, str] = {
    "month": "month",
    "monthly": "month",
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
}

#: Срок назван, но не узнан — пункт не берётся.
_UNKNOWN_PERIOD = "?"


@dataclass(frozen=True, slots=True)
class Offer:
    """Одна цена из ответа: что продают, для какой ниши, сколько и за какой срок."""

    product: str
    price: Decimal
    niche: str | None = None
    currency: str | None = None
    #: `month` или `year` — цена за срок; пусто — разовая.
    period: str | None = None

    @property
    def name(self) -> str:
        """За что цена — как в строке списка на экране: «guest post · casino»."""
        return f"{self.product} · {self.niche}" if self.niche else self.product

    def as_json(self) -> dict[str, str | None]:
        """Для JSONB и снимка разбора: цена строкой, как у снимка, — `Decimal`
        в JSON без потерь не ложится."""
        return {
            "product": self.product,
            "niche": self.niche,
            "price": str(self.price),
            "currency": self.currency,
            "period": self.period,
        }


def offers_from(raw: Any) -> tuple[Offer, ...]:
    """Список цен из ответа модели — не длиннее `MAX_OFFERS`.

    Не список — цен нет, и это видно в журнале: ключ обязателен, его
    отсутствие значит, что форма ответа поехала.
    """
    if not isinstance(raw, list):
        logger.warning("%s: список цен — не список: %r", TOPIC, raw)
        return ()
    offers = [offer for item in raw if (offer := _offer(item)) is not None]
    if len(offers) > MAX_OFFERS:
        logger.info("%s: цен в ответе %s — в списке первые %s", TOPIC, len(offers), MAX_OFFERS)
    return tuple(offers[:MAX_OFFERS])


def _offer(item: Any) -> Offer | None:
    """Один пункт списка. `None` — пункт не цена; почему — в журнале."""
    if not isinstance(item, dict):
        return _dropped("не объект", item)
    price = as_price(item.get("price"))
    if price is None:
        return _dropped("цена — не число больше нуля", item)
    if price >= IMPLAUSIBLE_PRICE:
        return _dropped("неправдоподобная цена", item)
    product = _phrase(item.get("product"))
    if product is None:
        return _dropped("не сказано, за что цена", item)
    period = _period(item.get("period"))
    if period == _UNKNOWN_PERIOD:
        return _dropped("срок не узнан", item)
    currency = item.get("currency")
    return Offer(
        product=product,
        price=price,
        niche=_phrase(item.get("niche")),
        currency=normalize_currency(currency if isinstance(currency, str) else None),
        period=period,
    )


def _dropped(why: str, item: Any) -> Offer | None:
    # Пункт выброшен — это видно в журнале: если таких станет много,
    # сломался разбор, а не письма.
    logger.warning("%s: пункт списка цен выброшен — %s: %r", TOPIC, why, item)
    return None


def _phrase(raw: Any) -> str | None:
    """Слова донора как есть: нижний регистр, пробелы схлопнуты, не длиннее
    `MAX_WORDS` слов и `MAX_CHARS` знаков. Не строка или пусто — `None`."""
    if not isinstance(raw, str):
        return None
    return " ".join(raw.lower().split()[:MAX_WORDS])[:MAX_CHARS].rstrip() or None


def _period(raw: Any) -> str | None:
    """Срок цены: `month`, `year` или `None` — разовая. Назван, но не узнан —
    `_UNKNOWN_PERIOD`."""
    key = str(raw).strip().lower() if raw is not None else ""
    if not key:
        return None
    return PERIODS.get(key, _UNKNOWN_PERIOD)
