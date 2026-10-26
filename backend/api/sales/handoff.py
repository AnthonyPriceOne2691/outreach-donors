"""«Передать телемаркетологу» у ответа продаж, который ждёт человека: `POST /sales/threads/{N}/handoff`.

**Право — то же, что у остальных действий с перепиской продаж**: ответа в переписке, черновика
агента и файлов к ответу (`api/threads/routes.py`, `files.py`, `api/agent/routes.py`) — `send`,
а у переписки продаж ещё и «Продажи» (`stage_access.on_thread`): лид уходит наружу, сделкой в Kommo
и сообщением в Telegram, и писем продаж ему больше нет. Первой стоит «Продажи»: маршрут раздела,
без права отказ — о разделе, как у всех маршрутов `/sales`.

**По диалогу, а не по ответу**: передача одна на диалог, и телемаркетолог получает последнее
письмо лида, какой бы ответ ни был открыт. Ответы диалога, которые ждут человека, снимаются с
ожидания словами решения — `features/sales/handoff_manual.py`.

**Ответ — передача, как сервер знает её сразу.** Задача Kommo и Telegram асинхронная: итог —
в строке `sales_handoffs`, её отдаёт и выдача переписки (`ThreadView.handoff`, `thread_handoff`).
Отказы — словами: диалога нет — 404; не продаж, лид уже передан, продажи выключены, в диалоге
нет ответа человека, у диалога нет лида — 409.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.handoff_schemas import HandedOverView, HandoffCard
from backend.api.stage_access import on_thread
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.sales import handoff, handoff_manual
from backend.features.sales.handoff import HandoffError

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: После `_seller`: право ответа в переписке — `send`, у переписки продаж и «Продажи».
_thread_sender = Depends(on_thread(Permission.SEND))


def handoff_queue() -> handoff_manual.Enqueue:
    """Постановка задачи передачи. Зависимостью — тест подменяет её: Redis в тестах нет."""
    return handoff.enqueue_handoff


@router.post(
    "/threads/{thread_id}/handoff",
    response_model=HandedOverView,
    summary="Передать лида телемаркетологу",
)
async def hand_over(
    thread_id: int,
    _: UserModel = _seller,
    author: UserModel = _thread_sender,
    enqueue: handoff_manual.Enqueue = Depends(handoff_queue),
    session: AsyncSession = Depends(db_session),
) -> HandedOverView:
    try:
        done = await handoff_manual.hand_over(
            session, thread_id, by=author.email, author_id=author.id, enqueue=enqueue
        )
    except handoff_manual.UnknownThreadError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (handoff_manual.HandoffRefusedError, HandoffError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return HandedOverView.of(done)


async def thread_handoff(session: AsyncSession, stage: Stage, thread_id: int) -> HandoffCard | None:
    """Передача лида — для выдачи переписки (`GET /threads/{N}`): только у переписки продаж."""
    if stage is not Stage.SALES:
        return None
    row = await handoff_manual.of_thread(session, thread_id)
    return None if row is None else HandoffCard.of(row)
