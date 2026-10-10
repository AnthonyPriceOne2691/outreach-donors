"""События доставки: что почта сделала с нашим письмом.

До этого модуля отправка кончалась словом «ушло». Что письмо
не доставлено, выяснялось только ответом-отказом, а доля отказов
по домену не считалась вовсе — то есть выгорание домена было видно
по тишине и больше никак.

**Событие ищет наше письмо по нашему номеру.** Он уезжает в
`custom_args` при отправке и возвращается в каждом событии. Искать
по адресу получателя нельзя: у донора адресов бывает несколько,
и один из них мог смениться между письмом и событием.

**А найденное письмо сверяется с адресом события** (аудит 10.10.2026).
Номер уникален только в нашей базе: стенд с ключом боевой учётки платформы
шлёт свои письма №17, база, поднятая из копии, раздаёт номера заново, — и
отказ или жалоба по чужому письму №17 хоронили адрес нашего донора, ставили
его в стоп-лист и портили долю отказов ящика. Событие, чей адрес — не адрес
получателя письма, — чужое: оно не трогает ничего и считается «без письма».
Сильнее адреса в событии нет ничего: в `custom_args` только номер, номер
платформы пуст ровно у писем с неизвестным исходом, которые событие и решает,
а `Message-ID` у повтора после возврата в очередь другой.

**Статус двигается только вперёд.** События приходят не по порядку
и повторяются: платформа доставляет их «хотя бы один раз». Запоздавший
`delivered` не должен откатывать письмо, которое уже отмечено
недоставленным, а повтор — удваивать счётчики. Журнал здоровья ящика
не статус: повтор события он узнаёт по номеру события у платформы.

**Событие решает исход письма, застрявшего в «отправляется»** (07.10.2026).
Связь оборвалась посреди передачи — ушло ли письмо, неизвестно
(`unknown_outcome.py`). Любое событие по нему доказывает, что платформа
письмо приняла: письмо сначала записывается ушедшим тем же путём, что
обычная отправка (`settle.py`), и только потом событие делает своё. Иначе
«доставлено» по такому письму оставляло его без времени ухода и без срока
добивки, а «не дошло» — мимо доли отказов его ящика.

**И письма, которое человек вернул в очередь, а оно ушло.** Событие по нему
снимает письмо с очереди и записывает ушедшим, и пачка второй раз его
не шлёт. Других писем в очереди с событиями не бывает: событие есть только
у письма, которое платформа приняла, а принятое в очередь возвращает лишь
человек — отказ платформы значит «не приняла».

**Отказ доставки и жалоба на спам — разные последствия.** Первый
означает «адреса нет»: контакт помечается негодным, и открывается
следующий адрес донора — не дошедшее письмо «писали» не считается,
и следующая сборка берёт следующий адрес (`attempts.py`). Вторая
означает «этот человек считает нас спамом»: донор уходит в стоп-лист
целиком, потому что писать ему второй раз — это следующая жалоба
и выгоревший домен.

**Домен паркуется по доле отказов, а не по их числу.** Один отказ
из двух писем — это пятьдесят процентов и ничего не значит; поэтому
доля считается только после того, как с домена ушло хоть сколько-то
писем (`OUTREACH_BOUNCE_PAUSE_MIN_SENT`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import assert_never

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.contacts.preference import DEAD
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import unknown_outcome
from backend.features.outreach import health
from backend.features.outreach.senders import disable

logger = logging.getLogger(__name__)

#: Что платформа называет недоставкой. «dropped» — она сама отказалась
#: слать (адрес в её собственном стоп-листе), «blocked» — принимающий
#: сервер отказал; для нас все три означают «письмо не дошло».
BOUNCE_EVENTS = frozenset({"bounce", "dropped", "blocked"})

#: События, которые платформа шлёт только о принятом письме: всё, что она
#: делает с письмом, начинается с приёма. Незнакомое событие не доказывает
#: ничего и, как и раньше, пропускается.
PROVES_ACCEPTED = BOUNCE_EVENTS | {
    "processed",
    "deferred",
    "delivered",
    "open",
    "click",
    "spamreport",
    "unsubscribe",
    "group_unsubscribe",
    "group_resubscribe",
}

#: Из каких состояний событие о приёме записывает письмо ушедшим: исход
#: неизвестен — или человек вернул письмо в очередь, не найдя его в журнале.
PENDING = frozenset({MessageStatus.SENDING, MessageStatus.QUEUED})

#: Из каких состояний письмо ещё можно отметить доставленным.
#: Запоздавший `delivered` поверх недоставки означал бы, что письмо
#: одновременно дошло и не дошло. «Отправляется» сюда не входит:
#: событие по такому письму сначала записывает его ушедшим.
BEFORE_DELIVERY = frozenset({MessageStatus.SENT})


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    """Одно событие платформы в том виде, в каком оно нам нужно."""

    kind: str
    message_id: int | None
    #: Кому письмо, по словам платформы. Сверяется с получателем письма с этим
    #: номером: не он — событие о чужом письме (`_foreign`).
    email: str
    reason: str | None = None
    #: Мягкий отказ: ящик переполнен, сервер занят. Адрес живой,
    #: и помечать его негодным нельзя.
    soft: bool = False
    #: Когда платформа это сделала. Пусто — время прихода события.
    at: datetime | None = None
    #: Номер события у платформы (`sg_event_id`): по нему журнал здоровья ящика узнаёт
    #: повтор пачки (`outreach/health.py`). Пусто — номера нет, событие идёт без отсева.
    event_id: str | None = None


@dataclass
class EventReport:
    """Что события сделали с базой. Числа — для лога и для ответа."""

    delivered: int = 0
    bounced: int = 0
    complained: int = 0
    unknown: int = 0
    #: Письма из «отправляется» и из очереди, которые событие записало ушедшими.
    resolved: int = 0
    paused_domains: list[str] = field(default_factory=list)

    @property
    def as_report(self) -> str:
        return (
            f"доставлено {self.delivered}, отказов {self.bounced}, "
            f"жалоб {self.complained}, без письма {self.unknown}, "
            f"исход выяснен {self.resolved}"
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
        await _settle_if_pending(session, message, event, moment, report)
        await _apply_one(session, message, event, moment, report)
        if message.sender_id is None:
            continue
        # Этап с мягкими сигналами судит ящик своей политикой (`outreach/health.py`).
        heard = await health.listen(session, message.sender_id, event, moment)
        if heard.paused is not None:
            report.paused_domains.append(heard.paused)
        if event.kind in BOUNCE_EVENTS and not heard.ruled:
            checked.add(message.sender_id)

    for sender_id in checked:
        parked = await _park_if_burning(session, sender_id, moment)
        if parked is not None:
            report.paused_domains.append(parked)

    logger.info("события доставки: %s", report.as_report)
    return report


async def _message_of(session: AsyncSession, event: DeliveryEvent) -> MessageModel | None:
    """Наше письмо события. Нет номера, нет письма с ним или письмо ушло не на адрес
    события — `None`: событие не наше, и последствий у него нет."""
    if event.message_id is None:
        return None
    message = await session.get(MessageModel, event.message_id)
    if message is None or await _foreign(session, message, event):
        return None
    return message


async def _foreign(session: AsyncSession, message: MessageModel, event: DeliveryEvent) -> bool:
    """Событие о чужом письме с нашим номером: адрес события — не адрес получателя.

    Получателя не узнать — событие идёт, как шло до сверки: верим номеру. Так у письма,
    чей контакт удалён, и у письма продаж без лида, — отказываться от их событий значило
    бы терять отказы и жалобы наших же писем.
    """
    if await _addressed(session, message, event.email) is not False:
        return False
    logger.warning(
        "события доставки: событие «%s» с номером письма №%s — о чужом письме: адрес "
        "события не адрес получателя, событие пропущено",
        event.kind,
        message.id,
        extra={
            "letter": message.id,
            "event": event.kind,
            "event_email": event.email,
            "event_id": event.event_id,
        },
    )
    return True


async def _addressed(session: AsyncSession, message: MessageModel, email: str) -> bool | None:
    """Ушло ли письмо на адрес `email`: да, нет — или `None`, получателя не узнать.

    Получатель — тот же, кого назвала отправка (`stages.recipient`): у доноров
    и рекламодателей — контакт письма, у продаж — лид переписки, а не контакт письма
    (ответ лиду уходит лиду, даже если написал секретарь). Лида знает модуль продаж,
    а почта его не импортирует: спрашивает мост (`stages.sales_threads_to`). Сверка —
    без регистра и краевых пробелов, так же сверяет адрес лида и модуль продаж.
    """
    stage = await _stage_of(session, message)
    if stage is None:
        return None
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            if message.contact_id is None:
                return None
            contact = await session.get(ContactModel, message.contact_id)
            if contact is None:
                return None
            return contact.email.strip().lower() == email.strip().lower()
        case Stage.SALES:
            if message.thread_id is None:
                return None
            # Модуль продаж не ответил — «не узнать», а не «чужое» (`sales_lead_threads`).
            threads = await stages.sales_lead_threads(session, email)
            return None if threads is None else message.thread_id in threads
        case _:
            assert_never(stage)


async def _stage_of(session: AsyncSession, message: MessageModel) -> Stage | None:
    """Этап письма — по его рассылке; рассылки нет — не узнать."""
    campaign = await session.get(CampaignModel, message.campaign_id)
    return None if campaign is None else campaign.stage


async def _settle_if_pending(
    session: AsyncSession,
    message: MessageModel,
    event: DeliveryEvent,
    moment: datetime,
    report: EventReport,
) -> None:
    """Письмо в «отправляется» или в очереди, а платформа о нём сообщила: записать его
    ушедшим.

    Проигрыш захвата — не конец: пока событие шло, человек мог вернуть письмо
    из «отправляется» в очередь, и тогда оно записывается уже оттуда — иначе
    пачка отправила бы ушедшее письмо второй раз. Каждое состояние — не больше
    одного раза: письмо, ушедшее из них, событие не догоняет."""
    if event.kind not in PROVES_ACCEPTED:
        return
    at = event.at or moment
    tried: set[MessageStatus] = set()
    while message.status in PENDING and message.status not in tried:
        tried.add(message.status)
        if await unknown_outcome.settle_by_event(session, message, kind=event.kind, at=at):
            report.resolved += 1
            return


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
    if await _stage_of(session, message) is Stage.SALES:
        # Письмо продаж ушло лиду переписки (`stages.recipient`), а контакт письма — тот,
        # кто ответил, например секретарь: отказ адреса лида его адрес не хоронит. Иначе
        # закрылся бы и чужой адрес, а с ним — этапы 1–2 домену, если он ещё и донор
        # (кросс-ревью продаж, 10.10.2026). Шаг цепочки погашен выше.
        return
    contact = await session.get(ContactModel, message.contact_id)
    if contact is not None:
        contact.verification_status = DEAD
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
