"""Запросы под приём ответов.

Собраны здесь по той же причине, что у остальной рассылки: ни конвейер,
ни веб-слой не должны знать, из скольких таблиц складывается «этот ответ
относится к тому письму».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming


class UnknownReplyError(ValueError):
    """Ответа с таким номером нет."""


@dataclass(frozen=True, slots=True)
class Addressee:
    """Наше письмо и всё, что нужно, чтобы применить последствия."""

    message: MessageModel
    thread: ThreadModel | None
    domain_id: int
    host: str
    stage: Stage


class ReplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- приём ---

    async def already_taken(self, inbound_message_id: str) -> bool:
        """Принимали ли уже это письмо.

        Провайдер доставляет вебхуки «хотя бы один раз» и повторяет их
        при сбое; без этой проверки повтор дал бы второй ответ, второй
        разбор и второй платный вызов модели.
        """
        if not inbound_message_id:
            return False
        rows = await self._session.execute(
            select(ReplyModel.id).where(ReplyModel.inbound_message_id == inbound_message_id)
        )
        return rows.first() is not None

    async def ours_by_provider_id(self, provider_ids: Sequence[str]) -> list[tuple[str, int]]:
        """Наши письма по идентификаторам у почты — запасной путь привязки."""
        if not provider_ids:
            return []
        rows = await self._session.execute(
            select(MessageModel.provider_message_id, MessageModel.id).where(
                MessageModel.provider_message_id.in_(list(provider_ids))
            )
        )
        return [(str(provider_id), message_id) for provider_id, message_id in rows.all()]

    async def addressee(self, message_id: int) -> Addressee | None:
        """Кому мы писали этим письмом."""
        rows = await self._session.execute(
            select(MessageModel, DomainModel.host, CampaignModel.stage)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(MessageModel.id == message_id)
        )
        found = rows.first()
        if found is None:
            return None

        message, host, stage = found
        thread = (
            await self._session.get(ThreadModel, message.thread_id)
            if message.thread_id is not None
            else None
        )
        return Addressee(
            message=message,
            thread=thread,
            domain_id=message.domain_id,
            host=host,
            stage=stage,
        )

    async def domain_of(self, reply: ReplyModel) -> int | None:
        """Чей это донор.

        Сначала по диалогу, потом по письму. Порядок важен: связь письма
        стирается при его удалении (`ondelete="SET NULL"`), а диалог
        остаётся — и ответ, потерявший письмо, всё равно принадлежит
        своему донору.

        Найдено живым прогоном: подтверждение разбора срабатывало,
        а цена в карточку донора не попадала, потому что искали только
        через письмо.
        """
        if reply.thread_id is not None:
            thread = await self._session.get(ThreadModel, reply.thread_id)
            if thread is not None:
                return thread.domain_id

        if reply.message_id is not None:
            message = await self._session.get(MessageModel, reply.message_id)
            if message is not None:
                return message.domain_id

        return None

    # --- последствия ---

    async def suppress(self, email: str, *, stage: Stage | None = None) -> None:
        """Адрес в стоп-лист. Донора целиком добавляет человек."""
        rows = await self._session.execute(
            select(SuppressionModel.id).where(SuppressionModel.email == email)
        )
        if rows.first() is not None:
            return
        self._session.add(
            SuppressionModel(
                email=email,
                reason=SuppressionReason.UNSUBSCRIBED,
                stage=stage,
                created_by="приём ответов",
            )
        )

    async def mark_contact_dead(self, contact_id: int | None) -> None:
        """Пометить адрес негодным. Следующий адрес донора открывается тем,
        что этот перестаёт быть лучшим."""
        if contact_id is None:
            return
        contact = await self._session.get(ContactModel, contact_id)
        if contact is not None:
            contact.verification_status = "bounced"
            contact.verification_score = 0

    async def remember_answering_address(
        self, *, domain_id: int, email: str, now: datetime | None = None
    ) -> ContactModel:
        """Адрес, с которого ответили, — к контактам донора и как
        предпочтительный: дальше пишем тому, кто отвечает."""
        moment = now or datetime.now(UTC)
        rows = await self._session.execute(
            select(ContactModel)
            .where(ContactModel.domain_id == domain_id)
            .where(ContactModel.email == email)
        )
        contact = rows.scalars().first()
        if contact is None:
            contact = ContactModel(domain_id=domain_id, email=email, source=ContactSource.MANUAL)
            self._session.add(contact)
            await self._session.flush()
        contact.last_replied_at = moment
        return contact

    async def store_price(
        self,
        *,
        domain_id: int,
        price: Decimal,
        currency: str | None,
        now: datetime | None = None,
    ) -> None:
        """Перенести цену в карточку донора.

        Цена кладётся в той валюте, в которой её назвали: конвертации
        в сервисе нет, и подписать евро долларами значит записать
        неверное число.
        """
        rows = await self._session.execute(
            select(DonorModel).where(DonorModel.domain_id == domain_id)
        )
        donor = rows.scalars().first()
        if donor is None:
            return
        donor.last_price = price
        donor.last_price_currency = currency
        donor.last_price_at = now or datetime.now(UTC)

    async def stop_chain(self, thread_id: int | None) -> int:
        """Остановить цепочку: ни одного следующего письма этому донору.

        Гасится и то, что стоит в очереди, и **срок у уже отправленного**:
        добивка не лежит в очереди заранее, она рождается по сроку. Пока
        срок цел, ответивший донор получит следующее письмо — то самое
        неуважение, ради запрета которого правило и написано.

        Возвращает, сколько писем это затронуло. Ноль — обычное дело:
        до добивок доходит меньшинство диалогов.
        """
        if thread_id is None:
            return 0
        rows = await self._session.execute(
            select(MessageModel).where(MessageModel.thread_id == thread_id)
        )
        stopped = 0
        for message in rows.scalars().all():
            if message.status is MessageStatus.QUEUED:
                message.status = MessageStatus.STOPPED
                message.next_action_at = None
                stopped += 1
            elif message.next_action_at is not None:
                message.next_action_at = None
                stopped += 1
        return stopped

    async def mark_bounced(self, message: MessageModel) -> None:
        """Пометить наше письмо не дошедшим.

        Статус двигается только вперёд: подтверждение доставки, пришедшее
        с опозданием, не должно перебивать отказ.
        """
        if message.status in (MessageStatus.SENT, MessageStatus.DELIVERED):
            message.status = MessageStatus.BOUNCED

    # --- запись ответа ---

    def add(
        self,
        incoming: Incoming,
        *,
        kind: ReplyKind,
        thread_id: int | None,
        message_id: int | None,
        found: Extracted | None,
    ) -> ReplyModel:
        """Записать ответ вместе с исходным текстом.

        Разобранное приходит одним значением, а не восемью полями: восемь
        полей рядом означают, что одно из них однажды забудут передать,
        и письмо ляжет в базу без цены, которую из него достали.
        """
        reply = ReplyModel(
            thread_id=thread_id,
            message_id=message_id,
            kind=kind,
            raw_body=incoming.text,
            inbound_message_id=incoming.message_id or None,
            from_email=incoming.from_email[:255],
            subject=incoming.subject[:512],
            attachments=[a.as_record() for a in incoming.attachments] or None,
            price_white=found.price_white if found else None,
            price_grey=found.price_grey if found else None,
            currency=found.currency if found else None,
            payment_methods=list(found.payment_methods) if found else None,
            confidence=found.confidence if found else None,
        )
        self._session.add(reply)
        return reply

    async def reply(self, reply_id: int) -> ReplyModel:
        found = await self._session.get(ReplyModel, reply_id)
        if found is None:
            raise UnknownReplyError(f"Ответа №{reply_id} нет")
        return found

    async def confirm(
        self,
        reply: ReplyModel,
        *,
        by: str,
        price_white: Decimal | None,
        price_grey: Decimal | None,
        currency: str | None,
        payment_methods: list[str] | None,
        now: datetime | None = None,
    ) -> None:
        """Подтвердить разбор руками.

        **Подтверждение человека сильнее любой уверенности модели.**
        Уверенность при этом не трогаем: она осталась тем, что сказала
        модель, и переписать её значило бы стереть след — потом никто
        не проверит, часто ли модель ошибается.
        """
        reply.price_white = price_white
        reply.price_grey = price_grey
        reply.currency = currency
        reply.payment_methods = payment_methods or None
        reply.reviewed_by = by[:128]
        reply.reviewed_at = now or datetime.now(UTC)

    async def unbound(self, *, limit: int = 100) -> Sequence[ReplyModel]:
        """Ответы, которые не удалось соотнести ни с одним нашим письмом."""
        rows = await self._session.execute(
            select(ReplyModel)
            .where(ReplyModel.thread_id.is_(None))
            .order_by(ReplyModel.id.desc())
            .limit(limit)
        )
        return rows.scalars().all()
