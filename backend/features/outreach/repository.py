"""Чтение рассылки: отправители и диалоги.

Запросы собраны здесь, а не в обработчиках: веб-слой не должен знать,
из скольких таблиц складывается строка списка. Заодно это единственный
способ проверить их без сервера — на настоящей базе, но без HTTP.

**Список и числа — этапов, которые видит спрашивающий** (`stages`, решение
Anthony 10.10.2026, П2): без права «Продажи» переписок продаж нет ни в списке,
ни в числах «Обзора» и меню. Сужает сама база — условием на этап рассылки.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Text, and_, case, func, select
from sqlalchemy import true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.outreach.threads import ThreadState, ThreadSummary, state_of, summarize
from backend.features.replies.inbound import MAX_TEXT_CHARS


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


@dataclass(frozen=True, slots=True)
class ThreadMark:
    """Диалог для чисел «Обзора» и меню: чей он, какого этапа и в каком состоянии."""

    thread_id: int
    domain_id: int
    stage: Stage
    state: ThreadState


@dataclass(frozen=True, slots=True)
class ListedLetter:
    """Письмо строки списка — то, что читает правило (`threads.LetterFacts`):
    статус и время ухода. Тема и тело остаются в базе (аудит 10.10.2026)."""

    status: MessageStatus
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class ListedReply:
    """Ответ строки списка и чисел меню — то, что читает правило (`threads.ReplyFacts`).

    Адреса, тема и список цен остаются в базе: правило их не читает. Текст
    и снимок разбора — только у тех ответов, где правило их читает (`_body_head`,
    `_snapshot`); у остальных вместо них пусто.
    """

    id: int
    kind: ReplyKind
    created_at: datetime
    confidence: float | None
    reviewed_at: datetime | None
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None
    placement: str | None
    raw_body: str
    model_parse: dict[str, Any] | None


def _body_head() -> ColumnElement[str]:
    """Начало текста — только у автоответа на письмо донору: в нём правило ищет
    сумму в валюте (`threads.review_of`), а у других ответов текст не читает.
    Ищет в первых `MAX_TEXT_CHARS` знаках (`outcome.names_a_sum`) — дальше не грузим:
    текст ответа бывает в двести тысяч знаков (`inbound.MAX_BODY_CHARS`)."""
    return case(
        (
            and_(CampaignModel.stage == Stage.DONORS, ReplyModel.kind == ReplyKind.AUTO_REPLY),
            func.left(ReplyModel.raw_body, MAX_TEXT_CHARS, type_=Text),
        ),
        else_="",
    )


def _snapshot() -> ColumnElement[Any]:
    """Снимок разбора — только у ответа продаж: из него правило читает, ждёт ли ответ
    человека и закрыт ли адрес (`outcome.sales_review`). Снимок модели у ответа донора
    правилу не нужен — и не грузится."""
    return case((CampaignModel.stage == Stage.SALES, ReplyModel.model_parse))


#: Все этапы — умолчание списка и чисел: кто их не сужает, видит всё, как до П2.
EVERY_STAGE: frozenset[Stage] = frozenset(Stage)


def _seen(stages: Collection[Stage]) -> ColumnElement[bool]:
    """Рассылка — видимого этапа. Видны все — условия нет, как до П2."""
    return sa_true() if set(stages) >= EVERY_STAGE else CampaignModel.stage.in_(stages)


def _seen_letters(stages: Collection[Stage]) -> ColumnElement[bool]:
    """Письмо — из диалога видимого этапа. Без соединения запроса писем с рассылкой: её
    этап спрашивает подзапрос по диалогам, и только когда видны не все."""
    if set(stages) >= EVERY_STAGE:
        return sa_true()
    return MessageModel.thread_id.in_(
        select(ThreadModel.id)
        .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
        .where(_seen(stages))
    )


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

    async def threads(self, *, stages: Collection[Stage] = EVERY_STAGE) -> list[ThreadRow]:
        """Все диалоги видимых этапов (`stages`), новые первыми, — списку «Диалогов».

        Список до 09.10.2026 брал двести новых: с двести первого старый диалог,
        в котором только что ответили, выпадал из списка, а главная его считала
        и вела на пустой фильтр (аудит экранов 09.10.2026).

        Письма и ответы — без текстов (аудит 10.10.2026): строке нужны состояние,
        счёт ушедших писем, время и цена, а грузились переписки целиком — тела писем,
        тексты ответов с адресами и разбором. На тысячах диалогов продаж это сотни
        мегабайт на каждый показ в процессе с потолком в гигабайт.
        """
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
            .where(_seen(stages))
            .order_by(ThreadModel.id.desc())
        )
        found = rows.all()
        if not found:
            return []

        letters = await self._listed_letters(stages)
        replies = await self._listed_replies(stages)
        return [
            ThreadRow(
                thread=thread,
                host=host,
                contact_email=email,
                campaign_name=campaign,
                summary=summarize(letters.get(thread.id, []), replies.get(thread.id, []), stage),
                stage=stage,
            )
            for thread, host, email, campaign, stage in found
        ]

    async def states(self, *, stages: Collection[Stage] = EVERY_STAGE) -> list[ThreadMark]:
        """Состояние каждого диалога видимых этапов (`stages`) — числам «Обзора» и меню.

        Правило то же, что у списка (`threads.state_of`), но из писем ему нужны
        только статусы — они и приходят из базы, парами «диалог — статус», без самих
        писем: меню спрашивает числа раз в минуту с каждой открытой вкладки, и до
        аудита 10.10.2026 каждый раз грузило переписки целиком.
        """
        threads = await self._session.execute(
            select(ThreadModel.id, ThreadModel.domain_id, CampaignModel.stage)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(_seen(stages))
        )
        statuses = await self._letter_statuses(stages)
        replies = await self._listed_replies(stages)
        return [
            ThreadMark(
                thread_id=thread_id,
                domain_id=domain_id,
                stage=stage,
                state=state_of(statuses.get(thread_id, set()), replies.get(thread_id, []), stage),
            )
            for thread_id, domain_id, stage in threads.tuples()
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
        """Письма целиком — карточке переписки."""
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

    async def _listed_letters(
        self, stages: Collection[Stage] = EVERY_STAGE
    ) -> dict[int, list[ListedLetter]]:
        """Письма всех диалогов — статусом и временем ухода, одним запросом.

        По запросу на диалог список стоил бы тысячи обращений к базе. И без перечня
        номеров диалогов: список берёт все, а перечень из тысяч номеров упёрся бы
        в потолок параметров запроса у asyncpg (32 767).
        """
        rows = await self._session.execute(
            select(MessageModel.thread_id, MessageModel.status, MessageModel.sent_at)
            .where(MessageModel.thread_id.is_not(None))
            .where(_seen_letters(stages))
        )
        by_thread: dict[int, list[ListedLetter]] = {}
        for thread_id, status, sent_at in rows.tuples():
            if thread_id is not None:
                by_thread.setdefault(thread_id, []).append(ListedLetter(status, sent_at))
        return by_thread

    async def _letter_statuses(
        self, stages: Collection[Stage] = EVERY_STAGE
    ) -> dict[int, set[MessageStatus]]:
        """Какие статусы есть у писем каждого диалога — парами из базы, без писем."""
        rows = await self._session.execute(
            select(MessageModel.thread_id, MessageModel.status)
            .where(MessageModel.thread_id.is_not(None))
            .where(_seen_letters(stages))
            .distinct()
        )
        by_thread: dict[int, set[MessageStatus]] = {}
        for thread_id, status in rows.tuples():
            if thread_id is not None:
                by_thread.setdefault(thread_id, set()).add(status)
        return by_thread

    async def _listed_replies(
        self, stages: Collection[Stage] = EVERY_STAGE
    ) -> dict[int, list[ListedReply]]:
        """Ответы всех диалогов — полями правила (`ListedReply`), одним запросом.

        Ответ без диалога (`replies/unbound.py`) сюда не входит: строки, к которой
        его приложить, нет, и считает его своя вкладка.
        """
        rows = await self._session.execute(
            select(
                ReplyModel.thread_id,
                ReplyModel.id,
                ReplyModel.kind,
                ReplyModel.created_at,
                ReplyModel.confidence,
                ReplyModel.reviewed_at,
                ReplyModel.price_white,
                ReplyModel.price_grey,
                ReplyModel.currency,
                ReplyModel.placement,
                _body_head().label("raw_body"),
                _snapshot().label("model_parse"),
            )
            .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(_seen(stages))
        )
        by_thread: dict[int, list[ListedReply]] = {}
        for row in rows:
            by_thread.setdefault(row.thread_id, []).append(
                ListedReply(
                    id=row.id,
                    kind=row.kind,
                    created_at=row.created_at,
                    confidence=row.confidence,
                    reviewed_at=row.reviewed_at,
                    price_white=row.price_white,
                    price_grey=row.price_grey,
                    currency=row.currency,
                    placement=row.placement,
                    raw_body=row.raw_body,
                    model_parse=row.model_parse,
                )
            )
        return by_thread


def state_counts(rows: Sequence[ThreadRow]) -> dict[ThreadState, int]:
    """Сколько диалогов в каждом состоянии — для сводки над списком."""
    counts: dict[ThreadState, int] = {}
    for row in rows:
        counts[row.summary.state] = counts.get(row.summary.state, 0) + 1
    return counts
