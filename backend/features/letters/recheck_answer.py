"""«Цена та же»: ответ донора на перезапрос цены подтверждает прежнюю цену.

Перезапрос просит назвать цену числом, но ответ бывает и без числа — «всё по-прежнему»,
«цена та же». Разбор такой ответ в карточку не кладёт: числа нет. Переписка тогда ждёт
человека (`threads.recheck_unpriced`), а не пишет «цена получена» старой ценой, и у него
в разборе ответа — действие «Цена та же» (проверка 12.10.2026).

**Что пишется.** Прежняя цена донора — та же сумма, валюта и список цен — ложится
в карточку **временем ответа**: последнее слово донора — по тому, когда он ответил
(`donors/price.py`), и с ним цена снова свежая, а обход по донору открывается сам. Ответ
помечается подтверждённым этой ценой, как при обычном подтверждении разбора
(`ReplyRepository.confirm`), а в журнале — кто, по какому ответу и какая цена.

**Отказы — словами и до записи:** ответ не на перезапрос или с ценой — его подтверждают
обычным разбором; уже подтверждён; перекрыт более поздним принятым ответом
(`confirmation.refuse_superseded`); у донора цены нет — подтверждать нечего.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.donors.manual_price import DonorRefusedError
from backend.features.outreach.threads import asked_again_at, recheck_unpriced
from backend.features.replies.confirmation import refuse_superseded
from backend.features.replies.extract import PLACEMENT_SELLS
from backend.features.replies.repository import ReplyRepository


class SamePriceRefusedError(DonorRefusedError):
    """«Цена та же» к этому ответу не относится — причина словами."""


@dataclass(frozen=True, slots=True)
class SamePrice:
    """Прежняя цена донора, подтверждённая ответом на перезапрос."""

    reply_id: int
    price: Decimal
    currency: str | None
    #: Когда донор ответил — с этой минуты цена снова свежая.
    at: datetime
    #: Легла ли цена в карточку: слово донора новее этого ответа её не пускает.
    stored: bool


async def prior_price(session: AsyncSession, thread_id: int) -> tuple[Decimal, str | None] | None:
    """Прежняя цена донора переписки — для кнопки «Цена та же» у её ответа на перезапрос.
    Пусто — перезапроса в переписке не было или у донора цены нет."""
    messages = (
        await session.scalars(select(MessageModel).where(MessageModel.thread_id == thread_id))
    ).all()
    if asked_again_at(messages) is None:
        return None
    row = (
        await session.execute(
            select(DonorModel.last_price, DonorModel.last_price_currency)
            .join(ThreadModel, ThreadModel.domain_id == DonorModel.domain_id)
            .where(ThreadModel.id == thread_id)
        )
    ).first()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


async def same_price(
    session: AsyncSession, reply_id: int, *, by: str, author_id: int | None
) -> SamePrice:
    """«Цена та же»: прежняя цена донора — временем ответа на перезапрос. Без фиксации —
    её делает вызывающий, вместе с журналом."""
    repository = ReplyRepository(session)
    reply = await repository.reply(reply_id)
    thread = await session.get(ThreadModel, reply.thread_id) if reply.thread_id else None
    if thread is None or await repository.stage_of(reply) is not Stage.DONORS:
        raise SamePriceRefusedError(
            f"Ответ №{reply_id} — не из переписки с донором: прежней цены у него нет."
        )
    messages = (
        await session.scalars(select(MessageModel).where(MessageModel.thread_id == thread.id))
    ).all()
    if not recheck_unpriced(reply, asked_again_at(messages)):
        raise SamePriceRefusedError(_not_for_this(reply))
    await refuse_superseded(session, reply)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == thread.domain_id))
    if donor is None or donor.last_price is None:
        raise SamePriceRefusedError(
            f"У донора переписки №{thread.id} цены нет — подтверждать нечего: впишите цену "
            "из письма."
        )
    price, currency, offers = donor.last_price, donor.last_price_currency, donor.last_offers
    await repository.confirm(
        reply,
        by=by,
        price_white=price,
        price_grey=None,
        currency=currency,
        payment_methods=reply.payment_methods,
    )
    stored = await repository.store_price(
        domain_id=thread.domain_id,
        price=price,
        currency=currency,
        offers=offers,
        at=reply.created_at,
    )
    await repository.record_seller_answer(
        domain_id=thread.domain_id, answer=PLACEMENT_SELLS, reply_id=reply.id, at=reply.created_at
    )
    await AccessRepository(session).record(
        AuditAction.PRICE_REVIEWED,
        author_id=author_id,
        target=f"reply:{reply.id}",
        details={
            "действие": "цена та же — прежняя цена подтверждена ответом на перезапрос",
            "цена": str(price),
            "валюта": currency,
            "кто": by,
            "легла в карточку": stored,
        },
    )
    return SamePrice(
        reply_id=reply.id, price=price, currency=currency, at=reply.created_at, stored=stored
    )


def _not_for_this(reply: ReplyModel) -> str:
    """Почему «Цена та же» не к этому ответу — словами."""
    if reply.reviewed_at is not None:
        return f"Ответ №{reply.id} уже разобран: {reply.reviewed_by} подтвердил его раньше."
    if reply.price_white is not None or reply.price_grey is not None:
        return f"В ответе №{reply.id} есть цена — подтвердите её в разборе ответа, а не прежнюю."
    return (
        f"Ответ №{reply.id} — не ответ донора на перезапрос цены: «Цена та же» подтверждает "
        "прежнюю цену только ответом, пришедшим после перезапроса."
    )
