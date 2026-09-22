"""Отправка одного письма из очереди.

Порядок шагов здесь — не стиль, а список оплаченных чужим опытом правил.

**Стоп-лист проверяется перед каждой отправкой, без исключений.** Не при
сборке очереди: между сборкой и нажатием кнопки проходят часы, и человек
за это время мог отписаться. Проверка при сборке остаётся — она экономит
вызовы модели, — но решает эта.

**Факт отправки пишется в базу до вызова почты, а не после.**
Иначе повтор после сбоя шлёт второе письмо тому же донору, а это жалоба
на спам. Поэтому письмо сначала переводится в «отправляется» и
фиксируется, и только потом уходит наружу.

**Отказ почты и обрыв связи — разные исходы.** Платформа сказала «нет» —
письмо точно не ушло, его можно вернуть в очередь. Связь оборвалась —
неизвестно, ушло или нет, и письмо остаётся в «отправляется»: человек
разберётся по журналу платформы. Вернуть его в очередь значило бы
отправить второе.

**Ящик выбирается в момент отправки**, а не при сборке: очередь общая,
и вставший ящик не должен блокировать свою часть.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, MessageStatus, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import chain, compose, reply_to, unsubscribe
from backend.features.letters.transport import Outgoing, Transport, TransportError
from backend.features.outreach import senders as sender_rules
from backend.features.outreach.repository import OutreachRepository

logger = logging.getLogger(__name__)


class SendError(RuntimeError):
    """Письмо не отправлено. Сообщение называет причину и что делать."""


class NotQueuedError(SendError):
    """Письмо не в очереди: уже ушло, остановлено или отправляется."""


class SuppressedError(SendError):
    """Донор или адрес в стоп-листе."""


class NotReadyError(SendError):
    """В письме остались незаполненные обязательные места."""


class NoSenderError(SendError):
    """Сегодня писать некому: все ящики выключены или выбрали дневной лимит."""


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """Чем кончилась отправка."""

    message_id: int
    sender_email: str
    provider_message_id: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool


@dataclass(frozen=True, slots=True)
class _Target:
    """Письмо вместе с тем, что нужно для отправки."""

    message: MessageModel
    host: str
    email: str
    stage: Stage


class Sending:
    """Отправка письма. Один экземпляр — один транспорт."""

    def __init__(
        self, session: AsyncSession, transport: Transport, *, now: datetime | None = None
    ) -> None:
        self._session = session
        self._transport = transport
        self._now = now

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def send(
        self,
        message_id: int,
        *,
        author_id: int | None = None,
        from_sender_id: int | None = None,
        in_reply_to: str | None = None,
    ) -> SendOutcome:
        """Отправить письмо из очереди.

        `from_sender_id` — ящик задан заранее. Так уходит добивка:
        переписку ведёт тот ящик, что её начал, и менять его на середине
        разговора значит попасть в спам и запутать собеседника.
        """
        target = await self._target(message_id)
        await self._check_suppression(target)
        self._check_ready(target)

        sender = (
            await self._pinned_sender(from_sender_id)
            if from_sender_id is not None
            else (await self._pick_sender(target.stage)).sender
        )

        # Факт отправки — в базе до вызова почты. Отдельная фиксация,
        # а не общая с вызывающим: между ней и почтой ничего не должно
        # остаться незаписанным.
        target.message.status = MessageStatus.SENDING
        target.message.sender_id = sender.id
        await self._session.commit()

        provider_id = await self._hand_over(target, sender, in_reply_to=in_reply_to)
        await self._settle(target, sender, provider_id=provider_id, author_id=author_id)
        return SendOutcome(
            message_id=target.message.id,
            sender_email=sender.email,
            provider_message_id=provider_id,
            real=self._transport.real,
        )

    # --- шаги ---

    async def _target(self, message_id: int) -> _Target:
        rows = await self._session.execute(
            select(MessageModel, DomainModel.host, ContactModel.email, CampaignModel.stage)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
            .where(MessageModel.id == message_id)
        )
        found = rows.first()
        if found is None:
            raise SendError(f"Письма №{message_id} нет")

        message, host, email, stage = found
        if message.status is not MessageStatus.QUEUED:
            raise NotQueuedError(
                f"Письмо №{message_id} в состоянии «{message.status.value}», а не в очереди. "
                "Отправить можно только то, что ещё ждёт отправки"
            )
        if not email:
            raise SendError(
                f"У письма №{message_id} нет адреса получателя: контакт удалён после сборки "
                "очереди. Письмо стоит убрать и собрать очередь заново"
            )
        return _Target(message=message, host=host, email=email, stage=stage)

    async def _check_suppression(self, target: _Target) -> None:
        """Стоп-лист на двух уровнях: адрес блокирует себя, донор — все свои
        адреса. Требование «больше не пишите» сильнее отписки от адреса."""
        rows = await self._session.execute(
            select(SuppressionModel)
            .where(
                or_(
                    SuppressionModel.domain_id == target.message.domain_id,
                    SuppressionModel.email == target.email,
                )
            )
            .where(
                or_(
                    SuppressionModel.stage.is_(None),
                    SuppressionModel.stage == target.stage,
                )
            )
        )
        found = rows.scalars().first()
        if found is not None:
            raise SuppressedError(
                f"Донору {target.host} писать нельзя: стоп-лист, причина «{found.reason.value}». "
                "Письмо стоит убрать из очереди"
            )

    def _check_ready(self, target: _Target) -> None:
        """Незаполненный юридический блок — отказ, а не предупреждение.

        Без физического адреса и рабочей отписки рассылка нарушает законы
        почти во всех целевых странах, а домены выгорают за недели.
        """
        unset = compose.unset_in(target.message.body or "")
        if unset:
            raise NotReadyError(
                f"В письме №{target.message.id} незаполненное: {', '.join(unset)}. "
                "Заполнить OUTREACH_SENDER_NAME, OUTREACH_POSTAL_ADDRESS "
                "и OUTREACH_UNSUBSCRIBE_URL и собрать очередь заново"
            )

    async def _cadence(self, campaign_id: int) -> list[int] | None:
        """Сроки добивок этой рассылки. Их задал человек при её создании."""
        campaign = await self._session.get(CampaignModel, campaign_id)
        return campaign.followup_days if campaign is not None else None

    async def _pinned_sender(self, sender_id: int) -> SenderModel:
        """Ящик, который ведёт эту переписку. Выключенный не подменяется
        другим: цепочка подождёт, пока его вернут, — второй голос
        в начатом разговоре хуже паузы."""
        sender = await self._session.get(SenderModel, sender_id)
        if sender is None:
            raise NoSenderError(
                f"Ящика №{sender_id} нет: им начата переписка, а его удалили. "
                "Письмо остаётся в очереди"
            )
        if not sender.enabled or sender.status is SenderStatus.PAUSED:
            why = "выключен" if not sender.enabled else "на паузе"
            raise NoSenderError(
                f"Ящик {sender.email} сейчас не пишет ({why}), а переписку ведёт он. "
                "Письмо остаётся в очереди"
            )
        return sender

    async def _pick_sender(self, stage: Stage) -> sender_rules.Availability:
        rows = await self._session.execute(select(SenderModel).order_by(SenderModel.id))
        moment = self._moment()
        spot = sender_rules.pick(
            rows.scalars().all(),
            sent_today=await self._sent_today(moment),
            stage=stage,
            now=moment,
        )
        if spot is None:
            raise NoSenderError(
                "Сегодня писать некому: все ящики либо выключены, либо выбрали дневной "
                "лимит. Письмо остаётся в очереди — завтра лимит откроется заново"
            )
        return spot

    async def _sent_today(self, moment: datetime) -> dict[int, int]:
        """Расход дневного капа. Считаются первые письма: у добивок свой
        часовой лейн, и класть их в тот же кап значит на каждую цепочку
        недосчитаться нового донора."""
        return await OutreachRepository(self._session).sent_today(now=moment, first_only=True)

    async def _hand_over(
        self, target: _Target, sender: SenderModel, *, in_reply_to: str | None = None
    ) -> str:
        """Отдать письмо почте. Отказ возвращает письмо в очередь."""
        outgoing = Outgoing(
            message_id=target.message.id,
            to=target.email,
            from_email=sender.email,
            from_name=compose.values_for(host=target.host)["sender_name"],
            reply_to=self._reply_to(target.message.id, sender.email),
            subject=target.message.subject or "",
            body=target.message.body or "",
            in_reply_to=in_reply_to,
            # Та же ссылка, что стоит в тексте письма: разойдись они —
            # кнопка почты отписывала бы не того, кому написали.
            unsubscribe_url=unsubscribe.url_for(target.message.domain_id),
        )
        try:
            return await self._transport.send(outgoing)
        except TransportError as exc:
            # Почта сказала «нет» — письмо точно не ушло, и его можно
            # вернуть в очередь без риска отправить второе.
            target.message.status = MessageStatus.QUEUED
            target.message.sender_id = None
            await self._session.commit()
            raise SendError(str(exc)) from exc
        except Exception:
            # Связь оборвалась: ушло или нет — неизвестно. Письмо остаётся
            # в «отправляется», потому что возврат в очередь означал бы
            # второе письмо тому же донору.
            logger.exception(
                "письма: письмо №%s осталось в состоянии «отправляется» — "
                "исход вызова почты неизвестен, разбирать по журналу платформы",
                target.message.id,
            )
            await self._session.commit()
            raise

    def _reply_to(self, message_id: int, sender_email: str) -> str | None:
        """Адрес «куда отвечать» с подписанной меткой.

        Настоящей отправке он обязателен: без метки ответ с чужого адреса
        не привязывается ни к чему и выглядит как «донор не ответил».
        Нулевому транспорту — нет: он никуда не пишет, и требовать ради
        него настроенный поддомен значило бы не дать посмотреть экран.
        """
        try:
            return reply_to.address_for(message_id, sender_email=sender_email)
        except reply_to.ReplyAddressError as exc:
            if self._transport.real:
                raise SendError(str(exc)) from exc
            logger.warning("письма: письмо №%s уходит без адреса ответа (%s)", message_id, exc)
            return None

    async def _settle(
        self,
        target: _Target,
        sender: SenderModel,
        *,
        provider_id: str,
        author_id: int | None,
    ) -> None:
        """Записать, что письмо ушло."""
        moment = self._moment()
        target.message.status = MessageStatus.SENT
        target.message.sent_at = moment
        target.message.provider_message_id = provider_id
        # Срок следующего письма цепочки назначается здесь, а не
        # вызывающим: вызывающих трое — экран, консоль и проход добивок, —
        # и правило, которое каждый из них обязан не забыть, однажды
        # забудут. Пусто означает, что цепочка кончилась.
        target.message.next_action_at = chain.due_after(
            moment, step=target.message.step, days=await self._cadence(target.message.campaign_id)
        )

        if target.message.contact_id is not None:
            contact = await self._session.get(ContactModel, target.message.contact_id)
            if contact is not None:
                contact.last_contacted_at = moment

        # Расход пишется и у нулевого транспорта: иначе по журналу
        # не отличить «не отправляли» от «отправили даром».
        usage.record(self._session, operation="letter_send", units=1)
        await AccessRepository(self._session).record(
            AuditAction.LETTER_SENT,
            author_id=author_id,
            target=f"message:{target.message.id}",
            details={
                "донор": target.host,
                "кому": target.email,
                "от кого": sender.email,
                "транспорт": self._transport.name,
                "ушло на самом деле": self._transport.real,
            },
        )
        await self._session.commit()
