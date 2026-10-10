"""База знаний и отправитель продаж: смотреть — `sales`, менять — `sales` и `send`, как в ядре.

Записи: список с версией базы, заведение, правка — и включение с выключением как
правка поля `active`; предпросмотр «что увидит агент» — той же выборкой, что получит
агент. Отправитель: чтение и запись целиком (`POST`, как у порогов: клиент экрана шлёт
JSON методами GET/POST/PATCH), с перечнем недостающего для отправки.
Логика, правила и журнал — `features/sales/kb.py` и `sender.py`: экран и консоль
зовут одно и то же. Отказы — словами через `api/errors.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.kb_schemas import (
    AgentView,
    KbEntryBody,
    KbEntryCard,
    KbEntryPatch,
    KbView,
    SenderBody,
    SenderView,
)
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import kb, sender

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`), и префикс
#: `/sales` с меткой «продажи» приходят оттуда — свои дали бы `/sales/sales/kb`.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: Запись базы — опора судьи (неверная пройдёт его проверку), подпись и адрес уходят в письме:
#: менять — ещё и с правом отправки, как письмо ядра. После `_seller`: без раздела отказ — о нём.
_sender = Depends(needs(Permission.SEND))


@router.get("/kb", response_model=KbView, summary="Записи базы знаний и версия базы")
async def list_entries(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> KbView:
    return KbView.of(await kb.entries(session), version=await kb.version(session))


@router.get("/kb/preview", response_model=AgentView, summary="Что увидит агент")
async def agent_preview(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> AgentView:
    return AgentView.of(await kb.facts(session))


@router.post(
    "/kb", response_model=KbEntryCard, status_code=status.HTTP_201_CREATED, summary="Завести запись"
)
async def add_entry(
    body: KbEntryBody,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> KbEntryCard:
    new = kb.entry(**body.model_dump())
    row = await kb.add(session, new, author=author.email, author_id=author.id)
    await session.commit()
    return KbEntryCard.of(row)


@router.patch(
    "/kb/{entry_id}", response_model=KbEntryCard, summary="Поправить, включить или выключить"
)
async def change_entry(
    entry_id: int,
    body: KbEntryPatch,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> KbEntryCard:
    # `null` в поле — «не трогать»: стереть заголовок или текст правкой нельзя.
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    row = await kb.change(session, entry_id, changes, author=author.email, author_id=author.id)
    await session.commit()
    return KbEntryCard.of(row)


@router.get("/sender", response_model=SenderView, summary="Отправитель продаж")
async def read_sender(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> SenderView:
    return SenderView.of(await sender.read(session))


@router.post("/sender", response_model=SenderView, summary="Записать отправителя целиком")
async def save_sender(
    body: SenderBody,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> SenderView:
    saved = await sender.save(session, body.model_dump(), author=author.email, author_id=author.id)
    await session.commit()
    return SenderView.of(saved)
