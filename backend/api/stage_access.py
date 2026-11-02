"""Право этапа на строках почты: письмо, переписка, ответ, черновик агента.

Решение Anthony 10.10.2026 (П2): право `sales` закрывает раздел «Продажи», маршруты модуля
и данные этапа продаж на общих экранах. Маршрут строки пускает сначала по своему праву
(`deps.needs`), затем — по этапу строки (`outreach/stage_of.py`): письмо, переписка, ответ
или черновик продаж без права «Продажи» — 403 словами, до тела маршрута. Строки нет —
пропуск дальше: «не найдено» скажет сам маршрут, как и до этой проверки. С 02.11.2026 так же
Этап 2 и право «Рекламодатели» (`access.permissions.STAGE_RIGHTS`): проверка одна, этап — строки.

Номер строки зависимость берёт из адреса своим именем (`letter_id`, `thread_id`…): FastAPI
отдаёт ей тот же параметр пути, что и маршруту. Этап из тела или строки запроса (пачка,
очередь этапа) маршрут проверяет сам — `require_stage` с теми же словами.

П2б (решение Anthony 10.10.2026) — так же ящик рассылки (`on_sender`) и запись стоп-листа
(`on_suppression`); исход задачи продаж проверяет маршрут задач — этап знает её исход.

Маршруты Этапа 2 целиком — `STAGE_TWO`: одно условие на весь роутер, и новый маршрут в нём
закрыт сам, а не потому, что его автор не забыл.
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

#: Что закрыто без права этапа — началом фразы отказа («… — только с правом «Продажи»»);
#: `{stage}` — этап строки в родительном падеже (`access.permissions.require_stage`).
LETTERS = "Письма {stage}"
THREADS = "Переписки {stage}"
REPLIES = "Ответы в переписках {stage}"
DRAFTS = "Черновики агента {stage}"
AGENT = "Настройки агента {stage}"
SENDERS = "Ящики {stage}"
SUPPRESSIONS = "Записи стоп-листа {stage}"
JOBS = "Задачи {stage}"

Guard = Callable[..., Awaitable[UserModel]]

#: Условие маршрута или роутера Этапа 2: раздел «Рекламодатели», обходы, доноры в обход, лиды.
STAGE_TWO = Depends(needs(Permission.ADVERTISERS))


def _passed(user: UserModel, stage: Stage | None, what: str) -> UserModel:
    """Сотрудник — дальше, к строке этапа `stage`; этап без его права — отказ словами."""
    require_stage(actor(user), stage, what)
    return user


def on_letter(permission: Permission) -> Guard:
    """Право `permission` — и право этапа у письма (`{letter_id}` очереди писем)."""

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
    """Право `permission` — и право этапа у переписки (`{thread_id}`)."""

    async def guard(
        thread_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_thread(session, thread_id), THREADS)

    return guard


def on_reply(permission: Permission) -> Guard:
    """Право `permission` — и право этапа у ответа в переписке (`{reply_id}`)."""

    async def guard(
        reply_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_reply(session, reply_id), REPLIES)

    return guard


def on_draft(permission: Permission) -> Guard:
    """Право `permission` — и право этапа у черновика агента (`{draft_id}`)."""

    async def guard(
        draft_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_draft(session, draft_id), DRAFTS)

    return guard


def on_sender(permission: Permission) -> Guard:
    """Право `permission` — и право этапа у ящика (`{sender_id}`, П2б)."""

    async def guard(
        sender_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_sender(session, sender_id), SENDERS)

    return guard


def on_suppression(permission: Permission) -> Guard:
    """Право `permission` — и право этапа у записи стоп-листа (`{row_id}`, П2б).
    Запись без этапа держит все этапы и пускается своим правом, как до П2б."""

    async def guard(
        row_id: int,
        user: UserModel = Depends(needs(permission)),
        session: AsyncSession = Depends(db_session),
    ) -> UserModel:
        return _passed(user, await stage_of.of_suppression(session, row_id), SUPPRESSIONS)

    return guard
