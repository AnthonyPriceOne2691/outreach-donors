"""Перезапрос цены у донора, чья цена старше срока свежести (`letters/price_recheck.py`).

Смотреть, кого можно перезапросить и куда уйдёт письмо, может каждый, у кого есть доступ
к базе. Перезапрос — письмо наружу, и право у него то же, что у отправки письма из
очереди и ответа в переписке (`send`). Этап — доноров: письмо уходит только в переписку
рассылки доноров или первым письмом своей рассылки доноров, и только донору, которого
обходит Этап 2 (принят, годен, не поставщик), — это проверяет ядро, а не экран. Письма
продаж в словах отказа называются только тому, у кого право «Продажи» (П2).

**Письма уходят сразу, а не задачей:** нажатие человека и есть согласование, а доноров
за раз — не больше потолка (`BATCH_MAX`). Итог — по каждому донору: что ушло и почему
не ушло остальное, словами сервера.

**«Цена та же»** — подтверждение прежней цены ответом донора на перезапрос
(`letters/recheck_answer.py`): право то же, что у подтверждения разбора цены (`prices`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.rechecks.schemas import (
    RecheckBody,
    RecheckCard,
    RecheckResult,
    RechecksView,
    SamePriceCard,
)
from backend.api.stage_access import on_reply
from backend.config import filters
from backend.config import outreach as outreach_cfg
from backend.features.access.permissions import sees_stage
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.letters import price_recheck, recheck_answer
from backend.features.letters.sending import Sending
from backend.features.letters.transport_factory import Transports, in_use

router = APIRouter(prefix="/price-rechecks", tags=["перезапрос цены"])

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))
#: Право подтверждать разбор цены — и «Продажи» у ответа продаж, как у подтверждения разбора.
_reply_reviewer = Depends(on_reply(Permission.PRICES))


def _sees_sales(user: UserModel) -> bool:
    """Письма продаж в словах отказа — только тому, кто видит продажи (П2)."""
    return sees_stage(actor(user), Stage.SALES)


@router.get("", response_model=RechecksView, summary="Доноры с протухшей ценой")
async def stale(
    limit: int = Query(default=100, ge=1, le=500),
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RechecksView:
    """Доноры, по которым обход не идёт из-за цены старше срока, — и можно ли перезапросить
    каждого сейчас, а если нет — почему."""
    found = await price_recheck.stale(session, limit=limit, sales_visible=_sees_sales(user))
    return RechecksView.of(
        found, ttl_days=filters.PRICE_TTL_DAYS, every_days=outreach_cfg.PRICE_RECHECK_DAYS
    )


@router.get("/{donor_id}", response_model=RecheckCard, summary="Перезапрос цены одного донора")
async def one(
    donor_id: int,
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> RecheckCard:
    """Можно ли перезапросить цену донора, куда и каким текстом уйдёт письмо — ничего
    не отправляя."""
    found = await price_recheck.look(session, donor_id, sales_visible=_sees_sales(user))
    return RecheckCard.of(found)


@router.post("", response_model=RecheckResult, summary="Перезапросить цену")
async def ask(
    body: RecheckBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> RecheckResult:
    """Перезапросить цену у названных доноров — письмом каждому, сразу. Кому нельзя —
    в итоге словами, остальные уходят."""
    async with in_use(Transports()) as transports:
        done = await price_recheck.ask_many(
            session,
            Sending(session, transports),
            body.donor_ids,
            author_id=author.id,
            sales_visible=_sees_sales(author),
        )
    return RecheckResult.of(done)


@router.post(
    "/replies/{reply_id}/same-price",
    response_model=SamePriceCard,
    summary="Цена та же — подтвердить прежнюю цену ответом на перезапрос",
)
async def same_price(
    reply_id: int,
    author: UserModel = _reply_reviewer,
    session: AsyncSession = Depends(db_session),
) -> SamePriceCard:
    """Ответ донора на перезапрос без числа — «цена та же»: прежняя цена донора ложится
    в карточку временем ответа и снова свежая; в журнале — кто и по какому ответу."""
    done = await recheck_answer.same_price(session, reply_id, by=author.email, author_id=author.id)
    await session.commit()
    return SamePriceCard.of(done)
