"""«Закрыть без ответа» у ответа продаж, который ждёт человека: `POST /sales/threads/{N}/close`.

Слово владельца 09.11.2026: автоответ службы поддержки, принятый ответом человека, и пустой ответ
снять с ожидания было нечем — только передачей телемаркетологу, а она завела бы сделку в Kommo.

**Право — как у передачи** (`handoff.py`): «Продажи» и `send` у переписки продаж — решение по
диалогу принимает тот, кто может в нём ответить. **Тело — ответ, который человек видел**
(`reply_id`): снимаются он и пришедшие раньше, а пришедший позже ждёт дальше
(`features/sales/close_manual.py`). Отказы словами: диалога нет — 404; не продаж, ответа нет в
диалоге, ничего не ждёт — 409.

**Ответ в переписке снимает ожидание тем же снимком** — `release_answered`: его зовут маршруты
ответа (`api/threads/routes.py`) и черновика (`api/agent/routes.py`) после отправки. Письмо к этому
месту уже ушло и записано, поэтому снятие идёт своей точкой сохранения: сбой снятия письма не
отменяет — ответ остаётся ждать человека, причина — в журнале, и ответ API о снятии ничего не говорит.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.stage_access import on_thread
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import close_manual

logger = logging.getLogger(__name__)

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: После `_seller`: право ответа в переписке — `send`, у переписки продаж и «Продажи».
_thread_sender = Depends(on_thread(Permission.SEND))


class CloseBody(BaseModel):
    """Ответ, который человек видел, когда закрывал: пришедшие позже он не читал."""

    reply_id: int


class ClosedView(BaseModel):
    """Итог «Закрыть без ответа»."""

    #: Ответы переписки, снятые с ожидания человека.
    released: list[int]
    #: Сколько ответов лида пришли позже и ждут человека дальше.
    later: int
    #: Итог словами — под кнопкой и в уведомлении.
    said: str


@router.post(
    "/threads/{thread_id}/close",
    response_model=ClosedView,
    summary="Закрыть ответ продаж без ответа",
)
async def close(
    thread_id: int,
    body: CloseBody,
    _: UserModel = _seller,
    author: UserModel = _thread_sender,
    session: AsyncSession = Depends(db_session),
) -> ClosedView:
    try:
        done = await close_manual.close(session, thread_id, reply_id=body.reply_id, by=author.email)
    except close_manual.UnknownThreadError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except close_manual.CloseRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return ClosedView(released=list(done.released), later=done.later, said=close_manual.said(done))


async def release_answered(
    session: AsyncSession, thread_id: int, reply_id: int, author: UserModel
) -> None:
    """Человек ответил в переписке на `reply_id`: ответы продаж по него перестают ждать человека.

    Переписка не продаж — ничего (`close_manual.answered`). Своя точка сохранения: сбой откатывает
    только снятие — письмо уже ушло и записано."""
    try:
        async with session.begin_nested():
            released = await close_manual.answered(
                session, thread_id, reply_id=reply_id, by=author.email
            )
        await session.commit()
    except Exception as exc:
        # Письмо ушло — сбой снятия его не отменяет. Точка сохранения уже откатила своё;
        # сессию закроет зависимость.
        logger.warning(
            "продажи: ответ в переписке ушёл, а ответы с ожидания человека не сняты",
            extra={"thread_id": thread_id, "reply_id": reply_id},
            exc_info=exc,
        )
        return
    if released:
        logger.info(
            "продажи: ответили в переписке — ответы больше не ждут человека",
            extra={"thread_id": thread_id, "released": released},
        )
