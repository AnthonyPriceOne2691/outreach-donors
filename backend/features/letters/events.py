"""События доставки: что почта сделала с нашим письмом.

До этого модуля отправка кончалась словом «ушло». Что письмо
не доставлено, выяснялось только ответом-отказом, а доля отказов
по домену не считалась вовсе — то есть выгорание домена было видно
по тишине и больше никак.

**Событие ищет наше письмо по нашему номеру.** Он уезжает в
`custom_args` при отправке и возвращается в каждом событии. Искать
по адресу получателя нельзя: у донора адресов бывает несколько,
и один из них мог смениться между письмом и событием.

**Статус двигается только вперёд.** События приходят не по порядку
и повторяются: платформа доставляет их «хотя бы один раз». Запоздавший
`delivered` не должен откатывать письмо, которое уже отмечено
недоставленным, а повтор — удваивать счётчики.

**Отказ доставки и жалоба на спам — разные последствия.** Первый
означает «адреса нет»: контакт помечается негодным, и открывается
следующий адрес донора. Вторая означает «этот человек считает нас
спамом»: донор уходит в стоп-лист целиком, потому что писать ему
второй раз — это следующая жалоба и выгоревший домен.

**Домен паркуется по доле отказов, а не по их числу.** Один отказ
из двух писем — это пятьдесят процентов и ничего не значит; поэтому
доля считается только после того, как с домена ушло хоть сколько-то
писем (`OUTREACH_BOUNCE_PAUSE_MIN_SENT`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, SuppressionReason
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.outreach.senders import disable

logger = logging.getLogger(__name__)

#: Что платформа называет недоставкой. «dropped» — она сама отказалась
#: слать (адрес в её собственном стоп-листе), «blocked» — принимающий
#: сервер отказал; для нас все три означают «письмо не дошло».
BOUNCE_EVENTS = frozenset({"bounce", "dropped", "blocked"})

#: Из каких состояний письмо ещё можно отметить доставленным.
#: Запоздавший `delivered` поверх недоставки означал бы, что письмо
#: одновременно дошло и не дошло.
BEFORE_DELIVERY = frozenset({MessageStatus.SENDING, MessageStatus.SENT})


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    """Одно событие платформы в том виде, в каком оно нам нужно."""

    kind: str
    message_id: int | None
    email: str
    reason: str | None = None
    #: Мягкий отказ: ящик переполнен, сервер занят. Адрес живой,
    #: и помечать его негодным нельзя.
    soft: bool = False


@dataclass
class EventReport:
    """Что события сделали с базой. Числа — для лога и для ответа."""

    delivered: int = 0
    bounced: int = 0
    complained: int = 0
    unknown: int = 0
    paused_domains: list[str] = field(default_factory=list)

    @property
    def as_report(self) -> str:
        return (
            f"доставлено {self.delivered}, отказов {self.bounced}, "
            f"жалоб {self.complained}, без письма {self.unknown}"
        )


async def apply_events(
    session: AsyncSession, events: list[DeliveryEvent], *, now: datetime | None = None
) -> EventReport:
    """Применить пачку событий. Возвращает отчёт, ничего не коммитит."""
    moment = now or datetime.now(UTC)
    report = EventReport()
    checked: set[int] = set()

    for event in events:
        message = await _message_of(session, event)
        if message is None:
            report.unknown += 1
            continue
        await _apply_one(session, message, event, moment, report)
        if event.kind in BOUNCE_EVENTS and message.sender_id is not None:
            checked.add(message.sender_id)

    for sender_id in checked:
        parked = await _park_if_burning(session, sender_id, moment)
        if parked is not None:
            report.paused_domains.append(parked)

    logger.info("события доставки: %s", report.as_report)
    return report


async def _message_of(session: AsyncSession, event: DeliveryEvent) -> MessageModel | None:
    if event.message_id is None:
        return None
    return await session.get(MessageModel, event.message_id)


async def _apply_one(
    session: AsyncSession,
    message: MessageModel,
    event: DeliveryEvent,
    moment: datetime,
    report: EventReport,
) -> None:
    if event.kind == "delivered":
        if message.status in BEFORE_DELIVERY:
            message.status = MessageStatus.DELIVERED
            message.delivered_at = moment
            report.delivered += 1
        return

    if event.kind in BOUNCE_EVENTS:
        await _bounced(session, message, event, report)
        return

    if event.kind == "spamreport":
        await _complained(session, message, report)


async def _bounced(
    session: AsyncSession, message: MessageModel, event: DeliveryEvent, report: EventReport
) -> None:
    """Письмо не дошло. Мягкий отказ адрес не хоронит."""
    if message.status is not MessageStatus.BOUNCED:
        message.status = MessageStatus.BOUNCED
        report.bounced += 1
    message.failure_reason = (event.reason or "отказ доставки")[:256]
    # Добивку слать некуда: следующий шаг цепочки гасится вместе с адресом.
    message.next_action_at = None

    if event.soft or message.contact_id is None:
        return
    contact = await session.get(ContactModel, message.contact_id)
    if contact is not None:
        contact.verification_status = "bounced"
        contact.verification_score = 0


async def _complained(session: AsyncSession, message: MessageModel, report: EventReport) -> None:
    """Жалоба на спам. Донор уходит целиком: второе письмо — вторая жалоба."""
    message.failure_reason = "жалоба на спам"
    message.next_action_at = None
    report.complained += 1

    found = await session.execute(
        select(SuppressionModel.id).where(SuppressionModel.domain_id == message.domain_id)
    )
    if found.first() is not None:
        return
    session.add(
        SuppressionModel(
            domain_id=message.domain_id,
            reason=SuppressionReason.COMPLAINED,
            created_by="жалоба на спам",
        )
    )


async def _park_if_burning(session: AsyncSession, sender_id: int, moment: datetime) -> str | None:
    """Домен с высокой долей отказов — на паузу. Возвращает адрес ящика."""
    sent, bounced = await _delivery_stats(session, sender_id)
    if sent < cfg.BOUNCE_PAUSE_MIN_SENT:
        return None
    rate = bounced / sent
    if rate < cfg.BOUNCE_PAUSE_THRESHOLD:
        return None

    sender = await session.get(SenderModel, sender_id)
    if sender is None or not sender.enabled:
        return None

    disable(sender, f"доля отказов {rate:.0%} — парковка", now=moment)
    logger.warning(
        "события доставки: %s снят с отправки — отказов %s из %s (%.0f%%)",
        sender.email,
        bounced,
        sent,
        rate * 100,
    )
    return sender.email


async def _delivery_stats(session: AsyncSession, sender_id: int) -> tuple[int, int]:
    """Сколько писем ушло с ящика и сколько из них не дошло.

    Считается по письмам, а не хранимым счётчиком: счётчик, который
    некому обнулять, однажды застревает — этот урок уже стоил дневного
    лимита ящиков (`SenderModel`).
    """
    rows = await session.execute(
        select(
            func.count(MessageModel.id),
            func.count(MessageModel.id).filter(MessageModel.status == MessageStatus.BOUNCED),
        ).where(
            MessageModel.sender_id == sender_id,
            MessageModel.sent_at.is_not(None),
        )
    )
    sent, bounced = rows.one()
    return int(sent or 0), int(bounced or 0)
