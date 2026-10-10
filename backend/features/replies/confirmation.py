"""Цена, которую человек вписал, подтверждая разбор ответа, — проверенная до записи.

Подтверждённая цена ложится в карточку донора теми же полями, что цена руками
(`donors/price.put_price`), и правило ввода у двух путей одно — то, что у
«Указать цену» (`donors/manual_price.typed_amount`, `typed_currency`): число
больше нуля, ниже потолка правдоподобия и до сотых, валюта — кодом, который знает
разбор ответов. Иначе одно и то же число одним путём записывалось бы, а другим нет.

До 10.10.2026 подтверждение брало любое число и любую строку валюты: «−5 EUR»
ложилось последней ценой донора, а 1e12 и «доллар США» не помещались в колонки
(`DECIMAL(10, 2)`, `String(8)`) и роняли запрос пятисоткой (проверка QA и аудит
10.10.2026). Отказ теперь — словами и до записи, как у цены руками.

**Валюта проверяется у цены.** «Цены в письме нет» подтверждают без цены, а поле
валюты при этом держит догадку модели: отказ «валюта не знакома» остановил бы
решение, к которому валюта не относится. Без цены она только приводится к коду,
как у разбора (`money.normalize_currency`), — и в колонку помещается всегда.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.features.donors.manual_price import typed_amount, typed_currency
from backend.features.replies.money import normalize_currency


@dataclass(frozen=True, slots=True)
class ConfirmedPrice:
    """Что подтвердил человек — проверенное."""

    white: Decimal | None
    grey: Decimal | None
    currency: str | None

    @property
    def main(self) -> Decimal | None:
        """Цена в карточку донора: белая, а без неё — серая."""
        return self.white if self.white is not None else self.grey


def confirmed_price(white: object, grey: object, currency: object) -> ConfirmedPrice:
    """Проверить вписанное. Не цена или не валюта — `ManualPriceError` словами."""
    checked_white, checked_grey = _amount_or_none(white), _amount_or_none(grey)
    said = currency.strip() if isinstance(currency, str) else ""
    if checked_white is None and checked_grey is None:
        return ConfirmedPrice(white=None, grey=None, currency=normalize_currency(said))
    return ConfirmedPrice(white=checked_white, grey=checked_grey, currency=typed_currency(said))


def _amount_or_none(raw: object) -> Decimal | None:
    """Пустое поле — цены нет, это законное решение; вписанное — цена по правилам."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return typed_amount(raw)
