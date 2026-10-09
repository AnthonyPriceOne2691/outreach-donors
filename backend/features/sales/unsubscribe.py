"""Отписка словами в треде продаж: адрес закрыт во всех направлениях.

Решение владельца «Правила рассылки»: лид-человек, попросивший не писать, не
давал согласия на письмо и в другой роли. Поэтому строка стоп-листа — без
этапа (`suppressions.stage NULL`, общий `ReplyRepository.suppress`), а всё
назначенное этому адресу — письма в очереди и сроки добивок в любом
направлении — снимается сразу (`letters.stoplist.stop_pending`), а не
отказом в минуту отправки. Диалог — «отписался».

**Ответил не лид — закрываются оба адреса** (решение владельца по ревью стыков): «уберите
нас» от ассистента или со второго ящика просит и за лида. Закрываются адрес ответившего и
адрес лида — тот, кому писали в диалоге: лид по явной связи (`sales_threads`), без неё —
контакт диалога. Писем продаж лиду строка `contacts` не знает — их снимает тот же
`stop_pending` по перепискам лида (мост почты).

Две дороги сюда, и обе без человека: правила приёма узнали отписку
(вид `unsubscribe`) — модель не зовётся; правила не узнали, а модель назвала
вид «просит не писать» с уверенностью не ниже порога. Для доноров не меняется
ничего: их отписку по-прежнему решает приём (`replies/outcome.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ThreadStatus
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.letters.stoplist import stop_pending
from backend.features.replies.repository import ReplyRepository
from backend.features.sales.models import SalesLeadModel, SalesThreadModel


@dataclass(frozen=True, slots=True)
class Closed:
    """Что сделала отписка."""

    #: Закрытые адреса: ответившего, а если ответил не лид — и адрес лида. Пусто — адреса нет.
    emails: tuple[str, ...]
    #: Сколько писем снято с очереди и сроков — во всех направлениях.
    stopped: int

    @property
    def words(self) -> str:
        if not self.emails:
            return "адреса у ответа нет — закрыть адрес руками"
        done = f"во всех направлениях, снято с очереди и сроков — {self.stopped}"
        if len(self.emails) == 1:
            return f"адрес закрыт {done}"
        sender, lead = self.emails
        return f"ответил не лид — закрыты оба адреса, {sender} и адрес лида {lead}, {done}"


def _clean(email: str | None) -> str:
    return (email or "").strip().lower()


async def close_address(session: AsyncSession, reply: ReplyModel) -> Closed:
    """Адрес ответившего и адрес лида — в стоп-лист без этапа, назначенное обоим — снять,
    диалог — «отписался»."""
    sender = _clean(reply.from_email)
    if not sender:
        return Closed((), 0)
    lead = _clean(await lead_address(session, reply.thread_id))
    emails = (sender,) if lead in ("", sender) else (sender, lead)
    repo = ReplyRepository(session)
    stopped = 0
    for email in emails:
        await repo.suppress(email)
        stopped += await stop_pending(session, email=email)
    stopped += await repo.stop_chain(reply.thread_id)
    thread = await session.get(ThreadModel, reply.thread_id) if reply.thread_id else None
    if thread is not None:
        thread.status = ThreadStatus.UNSUBSCRIBED
    return Closed(emails, stopped)


async def lead_address(session: AsyncSession, thread_id: int | None) -> str | None:
    """Кому писали в диалоге: лиду по явной связи (`sales_threads`), без неё — контакту диалога."""
    if thread_id is None:
        return None
    linked: str | None = await session.scalar(
        select(SalesLeadModel.email)
        .join(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesThreadModel.thread_id == thread_id)
    )
    if linked is not None:
        return linked
    contact: str | None = await session.scalar(
        select(ContactModel.email)
        .join(ThreadModel, ThreadModel.contact_id == ContactModel.id)
        .where(ThreadModel.id == thread_id)
    )
    return contact
