"""Цепочка писем продаж: смотреть — `sales`, менять — `sales` и `send`, как в ядре.

Набор целиком с цепочками по языкам (`GET /sales/chain`, гипотеза — параметром), запись
шага (`POST`: завести или поправить — ключ «набор, шаг, язык» знает ядро; клиент экрана
шлёт JSON методами GET/POST/PATCH) и предпросмотр письма с выдуманными значениями и
подписью из настроек отправителя — без записи. Правила, журнал и выбор набора —
`features/sales/chain.py`: экран и консоль зовут одно и то же. Отказы — словами через
`api/errors.py`: шаблон не годится — 400, метрики в тексте — 400, гипотезы нет — 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.chain_schemas import (
    ChainPreviewBody,
    ChainStepBody,
    ChainStepCard,
    ChainView,
    PreviewView,
)
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import chain, chain_text, sender

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как база знаний.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: Шаг уходит адресату, как письмо ядра (`api/letters/routes.py`): записать — ещё и с правом
#: отправки. Стоит после `_seller`: без раздела отказ называет раздел.
_sender = Depends(needs(Permission.SEND))


@router.get("/chain", response_model=ChainView, summary="Набор шаблонов и цепочки по языкам")
async def read_chain(
    hypothesis: int | None = Query(
        default=None, ge=1, description="номер гипотезы; без него — общий набор"
    ),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> ChainView:
    await chain.known(session, hypothesis)
    chains = [
        await chain.resolve(session, hypothesis_id=hypothesis, language=code)
        for code in chain_text.LANGUAGES
    ]
    return ChainView.of(hypothesis, await chain.rows(session, hypothesis), chains)


@router.post("/chain", response_model=ChainStepCard, summary="Записать шаг: завести или поправить")
async def save_step(
    body: ChainStepBody,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> ChainStepCard:
    new = chain_text.step_template(**body.model_dump(exclude={"hypothesis_id"}))
    row = await chain.save(
        session, new, hypothesis_id=body.hypothesis_id, author=author.email, author_id=author.id
    )
    await session.commit()
    return ChainStepCard.of(row)


@router.post("/chain/preview", response_model=PreviewView, summary="Письмо глазами адресата")
async def preview_step(
    body: ChainPreviewBody, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> PreviewView:
    new = chain_text.step_template(**body.model_dump())
    found = await sender.read(session)
    chain_text.unsigned(new, found)
    return PreviewView.of(chain_text.preview(new, found))
