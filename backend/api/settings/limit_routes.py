"""Панель лимитов на экране «Расход»: чтение, просчёт и смена — только администратору.

Требование другой стороны: «Панель должна быть доступна только для Администраторов!». Все три
маршрута — правом «менять потолки расхода» (`Permission.SPEND`): у роли оператора его нет. Без права
отказ словами, и экран вкладки не показывает: запроса к панели у него нет вовсе.

Своим файлом: маршруты «Расхода» у предела длины. Строки собирает `limit_panel.py`, здесь только
право и транзакция: просчёт ничего не пишет и не фиксирует, смена и запись журнала ложатся одной
транзакцией, а после неё память API перечитывает блок потолков (как `change_llm_caps`): смета,
посчитанная следом, видит новый лимит.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.settings import limit_panel
from backend.api.settings.limit_schemas import (
    LimitBody,
    LimitChangeBody,
    LimitPanelView,
    LimitRowCard,
    QuoteCard,
)
from backend.features.access.permissions import visible_stages
from backend.features.core import llm_caps
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel

router = APIRouter(tags=["настройки"])

_spender = Depends(needs(Permission.SPEND))


@router.get("/usage/limits", response_model=LimitPanelView, summary="Панель лимитов")
async def limit_rows(
    user: UserModel = _spender,
    session: AsyncSession = Depends(db_session),
) -> LimitPanelView:
    """Все лимиты сервиса по задачам: без вызовов провайдеров, только журнал расхода и снимки."""
    found = limit_panel.visible(await limit_panel.rows(session), visible_stages(actor(user)))
    return LimitPanelView(rows=[LimitRowCard.of(row) for row in found])


@router.post(
    "/usage/limits/quote",
    response_model=QuoteCard,
    summary="Просчёт нового лимита: сколько будет стоить, ничего не записав",
)
async def quote_limit(
    body: LimitBody,
    user: UserModel = _spender,
    session: AsyncSession = Depends(db_session),
) -> QuoteCard:
    """Что значит новое значение и сколько стоит. Значение вне границ — ответ с отказом словами, а
    не ошибка: человек поправит поле. Ничего не пишет."""
    limit_panel.guard(actor(user), body.key)
    return QuoteCard.of(await limit_panel.quote(session, body.key, body.field, body.value))


@router.post(
    "/usage/limits/change",
    response_model=QuoteCard,
    summary="Смена лимита после просчёта",
)
async def change_limit(
    body: LimitChangeBody,
    author: UserModel = _spender,
    session: AsyncSession = Depends(db_session),
) -> QuoteCard:
    """Новое значение — с этой минуты: сервер считает заново и отказывает словами, если просчёта
    не было, он устарел или значение стало недопустимым (`limit_panel.change`). Смена заводит строку
    истории и запись журнала той же транзакцией — теми же сеттерами, что у карточек лимитов."""
    limit_panel.guard(actor(author), body.key)
    done = await limit_panel.change(session, body.key, body.field, body.value, body.quoted, author)
    await session.commit()
    await llm_caps.refresh(session)
    return QuoteCard.of(done)
