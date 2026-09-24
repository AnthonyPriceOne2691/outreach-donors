"""Чтение рассылки: отправители и диалоги.

Запросы собраны здесь, а не в обработчиках: веб-слой не должен знать,
из скольких таблиц складывается строка списка. Заодно это единственный
способ проверить их без сервера — на настоящей базе, но без HTTP.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy import true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.outreach.threads import ThreadState, ThreadSummary, summarize


class UnknownSenderError(ValueError):
    """Отправителя с таким номером нет."""


class UnknownThreadError(ValueError):
    """Диалога с таким номером нет."""


@dataclass(frozen=True, slots=True)
class ThreadRow:
    """Строка списка диалогов: диалог вместе с тем, с кем он ведётся."""

    thread: ThreadModel
    host: str
    contact_email: str | None
    campaign_name: str
    summary: ThreadSummary
    #: Этап рассылки: у донора ответ — цена, у рекламодателя — лид.
    stage: Stage = Stage.DONORS


@dataclass(frozen=True, slots=True)
class ThreadDetail:
    """Переписка целиком: письма, входящие и сведённое состояние."""

    row: ThreadRow
    messages: Sequence[MessageModel]
    replies: Sequence[ReplyModel]


class OutreachRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- отправители ---

    async def senders(self) -> Sequence[SenderModel]:
        rows = await self._session.execute(
            select(SenderModel).order_by(SenderModel.domain, SenderModel.email)
        )
        return rows.scalars().all()

    async def sender(self, sender_id: int) -> SenderModel:
        found = await self._session.get(SenderModel, sender_id)
        if found is None:
            raise UnknownSenderError(f"Отправителя №{sender_id} нет")
        return found

    async def sent_today(
        self, *, now: datetime | None = None, first_only: bool = False
    ) -> dict[int, int]:
        """Сколько писем ушло сегодня с каждого ящика.

        Считается по письмам, а не по счётчику в строке отправителя.
        Такой счётчик был, и его никто не обнулял: к концу первых суток
        он упирался в кап и оставался там навсегда — ящик переставал
        получать письма, не сказав ни слова.

        Сутки считаются по UTC, как и всё остальное время в базе.
        Отправители живут в разных часовых поясах только в воображении:
        почтовая платформа смотрит на скорость, а не на местный полдень.
        """
        moment = now or datetime.now(UTC)
        since = datetime.combine(moment.date(), time.min, tzinfo=UTC)
        rows = await self._session.execute(
            select(MessageModel.sender_id, func.count())
            .where(MessageModel.sender_id.is_not(None))
            .where(MessageModel.sent_at >= since)
            # `first_only` — счёт для дневного капа. Добивки в кап
            # не входят (решение 21.09.2026): у них свой часовой потолок,
            # иначе цепочки съедают квоту новых доноров.
            .where(MessageModel.step == 0 if first_only else sa_true())
            .group_by(MessageModel.sender_id)
        )
        return {sender_id: count for sender_id, count in rows.all() if sender_id is not None}

    async def enabled_domains(self) -> set[str]:
        """Домены, у которых хоть один ящик включён. Нужно ровно для одного
        вопроса: останется ли чем отправлять после выключения этого."""
        rows = await self._session.execute(
            select(SenderModel.domain).where(SenderModel.enabled.is_(True)).distinct()
        )
        return set(rows.scalars().all())

    # --- диалоги ---

    async def threads(self, *, limit: int = 200) -> list[ThreadRow]:
        rows = await self._session.execute(
            select(
                ThreadModel,
                DomainModel.host,
                ContactModel.email,
                CampaignModel.name,
                CampaignModel.stage,
            )
            .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == ThreadModel.contact_id)
            .options(selectinload(ThreadModel.replies))
            .order_by(ThreadModel.id.desc())
            .limit(limit)
        )
        found = rows.all()
        if not found:
            return []

        messages = await self._messages_by_thread([thread.id for thread, *_ in found])
        return [
            ThreadRow(
                thread=thread,
                host=host,
                contact_email=email,
                campaign_name=campaign,
                summary=summarize(messages.get(thread.id, []), thread.replies, stage),
                stage=stage,
            )
            for thread, host, email, campaign, stage in found
        ]

    async def thread(self, thread_id: int) -> ThreadDetail:
        rows = await self._session.execute(
            select(
                ThreadModel,
                DomainModel.host,
                ContactModel.email,
                CampaignModel.name,
                CampaignModel.stage,
            )
            .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == ThreadModel.contact_id)
            .options(selectinload(ThreadModel.replies))
            .where(ThreadModel.id == thread_id)
        )
        found = rows.first()
        if found is None:
            raise UnknownThreadError(f"Диалога №{thread_id} нет")

        thread, host, email, campaign, stage = found
        messages = (await self._messages_by_thread([thread.id])).get(thread.id, [])
        # Письма и входящие идут вперемешку по времени — как в переписке.
        replies = sorted(thread.replies, key=lambda r: r.created_at)
        return ThreadDetail(
            row=ThreadRow(
                thread=thread,
                host=host,
                contact_email=email,
                campaign_name=campaign,
                summary=summarize(messages, replies, stage),
                stage=stage,
            ),
            messages=messages,
            replies=replies,
        )

    async def _messages_by_thread(self, thread_ids: Sequence[int]) -> dict[int, list[MessageModel]]:
        """Письма одним запросом на весь список.

        По запросу на диалог список из двухсот строк стоил бы двухсот
        обращений к базе — и это было бы незаметно на демонстрации
        и заметно на боевом объёме.
        """
        rows = await self._session.execute(
            select(MessageModel)
            .where(MessageModel.thread_id.in_(thread_ids))
            .order_by(MessageModel.step, MessageModel.id)
        )
        by_thread: dict[int, list[MessageModel]] = {}
        for message in rows.scalars().all():
            if message.thread_id is not None:
                by_thread.setdefault(message.thread_id, []).append(message)
        return by_thread


def state_counts(rows: Sequence[ThreadRow]) -> dict[ThreadState, int]:
    """Сколько диалогов в каждом состоянии — для сводки над списком."""
    counts: dict[ThreadState, int] = {}
    for row in rows:
        counts[row.summary.state] = counts.get(row.summary.state, 0) + 1
    return counts
