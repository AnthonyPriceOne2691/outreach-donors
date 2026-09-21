"""Подтверждение разбора цены человеком.

Ветка, без которой не держится приёмка: Э1-24 требует, чтобы разбор
с недостаточной уверенностью уходил в ручную очередь, а не в базу.
Здесь эта очередь и заканчивается.

**Подтверждение человека сильнее любой уверенности модели.** Подтверждая,
человек видит исходный текст письма рядом с разобранным — так устроена
карточка диалога, — и его решение кладёт цену в карточку донора
независимо от того, что насчитала модель.

**Уверенность модели при этом не переписывается.** Она осталась тем, что
модель сказала, и стереть её значило бы потерять единственный след,
по которому потом видно, часто ли она ошибается.

**Разбор — именованное действие.** Смотреть цены может каждый (так в ТЗ),
подтверждать — действие `prices`. Названо отдельно не чтобы отобрать,
а чтобы на вопрос «кто подтверждает цены» отвечал список действий,
а не чтение обработчиков.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.replies.schemas import ReviewBody, Reviewed
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.replies.repository import ReplyRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/replies", tags=["ответы"])

_reviewer = Depends(needs(Permission.PRICES))


@router.patch("/{reply_id}", response_model=Reviewed, summary="Подтвердить разбор цены")
async def review(
    reply_id: int,
    body: ReviewBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> Reviewed:
    """Принять цену такой, какой её увидел человек."""
    repository = ReplyRepository(session)
    reply = await repository.reply(reply_id)

    await repository.confirm(
        reply,
        by=author.email,
        price_white=body.price_white,
        price_grey=body.price_grey,
        currency=body.currency,
        payment_methods=body.payment_methods,
    )

    price = body.price_white if body.price_white is not None else body.price_grey
    domain_id = await repository.domain_of(reply)
    stored = False
    if price is not None and domain_id is not None:
        await repository.store_price(domain_id=domain_id, price=price, currency=body.currency)
        stored = True

    await AccessRepository(session).record(
        AuditAction.PRICE_REVIEWED,
        author_id=author.id,
        target=f"reply:{reply_id}",
        details={
            "действие": "разбор цены подтверждён",
            "белая": str(body.price_white) if body.price_white is not None else None,
            "серая": str(body.price_grey) if body.price_grey is not None else None,
            "валюта": body.currency,
            "уверенность модели": reply.confidence,
        },
    )
    await session.commit()

    logger.info("разбор: ответ №%s подтверждён, цена в базу — %s", reply_id, stored)
    return Reviewed(id=reply_id, reviewed_by=author.email, stored_price=stored)
