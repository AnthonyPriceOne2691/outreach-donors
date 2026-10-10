"""Право «Продажи» на строках почты: письмо, переписка, ответ, черновик агента.

Решение Anthony 10.10.2026 (П2): право `sales` закрывает раздел «Продажи», маршруты модуля
и данные этапа продаж на общих экранах. Маршрут строки пускает сначала по своему праву
(`deps.needs`), затем — по этапу строки (`outreach/stage_of.py`): письмо, переписка, ответ
или черновик продаж без права «Продажи» — 403 словами, до тела маршрута. Строки нет —
пропуск дальше: «не найдено» скажет сам маршрут, как и до этой проверки.

Номер строки зависимость берёт из адреса своим именем (`letter_id`, `thread_id`…): FastAPI
отдаёт ей тот же параметр пути, что и маршруту. Этап из тела или строки запроса (пачка,
очередь этапа) маршрут проверяет сам — `require_stage` с теми же словами.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.features.access.permissions import require_stage
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.outreach import stage_of

#: Что закрыто без права «Продажи» — началом фразы отказа («… — только с правом «Продажи»»).
LETTERS = "Письма продаж"
THREADS = "Переписки продаж"
REPLIES = "Ответы в переписках продаж"
DRAFTS = "Черновики агента продаж"
AGENT = "Настройки агента продаж"

Guard = Callable[..., Awaitable[UserModel]]


def _passed(user: UserModel, stage: Stage | None, what: str) -> UserModel:
    """Сотрудник — дальше, к строке этапа `stage`; продажи без права — отказ словами."""
    require_stage(actor(user), stage, what)
    return user


def on_letter(permission: Permission) -> Guard:
    """Право `permission` — и «Продажи» у письма продаж (`{letter_id}` очереди писем)."""

    async def guard(
        letter_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_message(session, letter_id), LETTERS)

    return guard


def on_message(permission: Permission) -> Guard:
    """То же для нашего письма в переписке (`{message_id}`): вложение ушедшего письма."""

    async def guard(
        message_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_message(session, message_id), LETTERS)

    return guard


def on_thread(permission: Permission) -> Guard:
    """Право `permission` — и «Продажи» у переписки продаж (`{thread_id}`)."""

    async def guard(
        thread_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_thread(session, thread_id), THREADS)

    return guard


def on_reply(permission: Permission) -> Guard:
    """Право `permission` — и «Продажи» у ответа в переписке продаж (`{reply_id}`)."""

    async def guard(
        reply_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_reply(session, reply_id), REPLIES)

    return guard


def on_draft(permission: Permission) -> Guard:
    """Право `permission` — и «Продажи» у черновика агента продаж (`{draft_id}`)."""

    async def guard(
        draft_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_draft(session, draft_id), DRAFTS)

    return guard
