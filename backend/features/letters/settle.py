"""Письмо ушло: что записывается в эту минуту — одним путём на все случаи.

Ушедшим письмо становится тремя путями: платформа ответила на отправку
(`sending.Sending`), её событие пришло по письму с неизвестным исходом, человек
нашёл письмо в её журнале (`unknown_outcome.py`). Запись одна на все три
(07.10.2026): время ухода, срок добивки, «писали» у адреса, расход и журнал.
Две копии разошлись бы на первой правке — письмо, отмеченное человеком,
осталось бы без добивки или мимо дневного лимита ящика, и разница всплыла бы
через неделю молчащей цепочкой.

**Из «отправляется» письмо выходит захватом**, как и входит в него
(`Sending._claim`): условный `UPDATE … WHERE status = 'sending'`. Событие
платформы может прийти раньше ответа на отправку, человек — нажать вместе
с ним. Второй путь видит «уже не отправляется» и второй записи «ушло» —
второго расхода и второй строки журнала об одном письме — не делает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import chain

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Witness:
    """Кому и чем ушло письмо — для журнала."""

    host: str
    email: str | None
    sender_email: str | None
    transport: str
    real: bool
    #: Как узнали, что ушло, если не из ответа платформы на отправку.
    how: str | None = None


async def leave_sending(session: AsyncSession, message: MessageModel, **values: Any) -> bool:
    """Вывести письмо из «отправляется» — только если оно всё ещё там.

    Ложь — его секундой раньше вывел другой путь, и писать поверх нельзя.
    """
    left = await session.execute(
        update(MessageModel)
        .where(MessageModel.id == message.id, MessageModel.status == MessageStatus.SENDING)
        .values(**values)
        .returning(MessageModel.id)
        # Как у захвата в `Sending._claim`: прочитанное письмо получает новые
        # значения, только если строка захвачена, — иначе сессия считала бы
        # его вышедшим, когда в базе оно уже в другом состоянии.
        .execution_options(synchronize_session="fetch")
    )
    return left.first() is not None


async def record_sent(
    session: AsyncSession,
    message: MessageModel,
    *,
    moment: datetime,
    provider_id: str | None,
    author_id: int | None,
    witness: Witness,
) -> bool:
    """Записать, что письмо ушло в `moment`. Без фиксации — её делает вызывающий.

    Ложь — письмо уже записал ушедшим другой путь (или вернул в очередь
    человек); тогда здесь не пишется ничего, кроме номера платформы: его
    знает только ответ на отправку.
    """
    if not await leave_sending(session, message, status=MessageStatus.SENT):
        if provider_id:
            message.provider_message_id = provider_id
        logger.warning(
            "письма: письмо №%s уже не «отправляется» — его исход записал другой путь",
            message.id,
        )
        return False

    message.sent_at = moment
    message.provider_message_id = provider_id
    # Срок следующего письма цепочки назначается здесь, а не вызывающим:
    # вызывающих много — экран, консоль, проход добивок, событие платформы,
    # человек по журналу, — и правило, которое каждый из них обязан
    # не забыть, однажды забудут. Пусто означает, что цепочка кончилась.
    message.next_action_at = chain.due_after(
        moment, step=message.step, days=await _cadence(session, message.campaign_id)
    )

    if message.contact_id is not None:
        contact = await session.get(ContactModel, message.contact_id)
        if contact is not None:
            contact.last_contacted_at = moment

    # Расход пишется и у нулевого транспорта: иначе по журналу
    # не отличить «не отправляли» от «отправили даром».
    usage.record(session, operation="letter_send", units=1)
    details: dict[str, Any] = {
        "донор": witness.host,
        "кому": witness.email,
        "от кого": witness.sender_email,
        "транспорт": witness.transport,
        "ушло на самом деле": witness.real,
    }
    if witness.how is not None:
        details["как узнали"] = witness.how
    await AccessRepository(session).record(
        AuditAction.LETTER_SENT,
        author_id=author_id,
        target=f"message:{message.id}",
        details=details,
    )
    return True


async def _cadence(session: AsyncSession, campaign_id: int) -> list[int] | None:
    """Сроки добивок рассылки. Их задал человек при её создании."""
    campaign = await session.get(CampaignModel, campaign_id)
    return campaign.followup_days if campaign is not None else None
