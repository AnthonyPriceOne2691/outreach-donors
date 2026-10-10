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

**Перекрытый ответ не подтверждают** (`threads.superseded_by`): позже донор назвал
цену, и она принята — она и лежит в карточке донора. Экран такой ответ разбирать
не зовёт, но форма могла остаться открытой с тех пор, как более поздний ответ ещё
не пришёл: подтверждение записало бы в карточку прежнюю цену поверх новой
(проверка прода 10.10.2026). Отказ — словами и до записи; решение по самому
ответу ничего бы не дало — цену переписки задаёт более поздний.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outreach import ReplyModel
from backend.features.donors.manual_price import typed_amount, typed_currency
from backend.features.outreach.threads import ReplyFacts, superseded_by
from backend.features.replies.money import normalize_currency


class SupersededReplyError(ValueError):
    """Ответ перекрыт более поздним с принятой ценой: подтверждать его нечего."""


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


async def refuse_superseded(session: AsyncSession, reply: ReplyModel) -> None:
    """Перекрытый ответ — отказ словами (`SupersededReplyError`), до любой записи."""
    if reply.thread_id is None:
        return
    rows = await session.scalars(select(ReplyModel).where(ReplyModel.thread_id == reply.thread_id))
    later = superseded_by(reply, rows.all())
    if later is not None:
        raise SupersededReplyError(_superseded_words(reply.id, later))


def _superseded_words(reply_id: int, later: ReplyFacts) -> str:
    price = later.price_white if later.price_white is not None else later.price_grey
    said = "" if price is None else f" {_amount(price)} {later.currency or ''}".rstrip()
    return (
        f"Ответ №{reply_id} перекрыт: позже донор назвал цену{said}, и она принята — цена "
        "переписки теперь из того ответа. Подтверждение этого записало бы в карточку донора "
        "прежнюю цену поверх новой; если новая цена неверна, поправьте её в более позднем ответе."
    )


def _amount(price: Decimal) -> str:
    """Цена словами: «150», «150,50» — без лишних нулей и с запятой, как пишет человек."""
    return f"{price:.2f}".removesuffix(".00").replace(".", ",")
