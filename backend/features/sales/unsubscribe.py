"""Отписка словами в треде продаж: адрес закрыт во всех направлениях.

Решение владельца «Правила рассылки»: лид-человек, попросивший не писать, не
давал согласия на письмо и в другой роли. Поэтому строка стоп-листа — без
этапа (`suppressions.stage NULL`, общий `ReplyRepository.suppress`), а всё
назначенное этому адресу — письма в очереди и сроки добивок в любом
направлении — снимается сразу (`letters.stoplist.stop_pending`), а не
отказом в минуту отправки. Диалог — «отписался».

Две дороги сюда, и обе без человека: правила приёма узнали отписку
(вид `unsubscribe`) — модель не зовётся; правила не узнали, а модель назвала
вид «просит не писать» с уверенностью не ниже порога. Для доноров не меняется
ничего: их отписку по-прежнему решает приём (`replies/outcome.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.letters.stoplist import stop_pending
from backend.features.replies.repository import ReplyRepository


@dataclass(frozen=True, slots=True)
class Closed:
    """Что сделала отписка."""

    email: str | None
    #: Сколько писем снято с очереди и сроков — во всех направлениях.
    stopped: int

    @property
    def words(self) -> str:
        if self.email is None:
            return "адреса у ответа нет — закрыть адрес руками"
        return f"адрес закрыт во всех направлениях, снято с очереди и сроков — {self.stopped}"


async def close_address(session: AsyncSession, reply: ReplyModel) -> Closed:
    """Адрес ответившего — в стоп-лист без этапа, назначенное — снять, диалог — «отписался»."""
    email = (reply.from_email or "").strip().lower()
    if not email:
        return Closed(None, 0)
    await ReplyRepository(session).suppress(email)
    stopped = await stop_pending(session, email=email)
    stopped += await ReplyRepository(session).stop_chain(reply.thread_id)
    thread = await session.get(ThreadModel, reply.thread_id) if reply.thread_id else None
    if thread is not None:
        thread.status = ThreadStatus.UNSUBSCRIBED
    return Closed(email, stopped)
