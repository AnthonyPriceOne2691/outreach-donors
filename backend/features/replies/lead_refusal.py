"""«В отказ» — решение человека по лиду рекламодателя, который ждёт человека.

Слово владельца 25.11.2026: ответ рекламодателя «спасибо, нам это не интересно» висел
в «Диалогах с рекламодателями» как «лид — ждёт человека», а снять его можно было только «Взять в
работу» — и лид уходил в CRM. Отказ снимает ответ с ожидания теми же `reviewed_by`/`reviewed_at`, что
«взять» (по ним «ждёт человека» считают все этапы), и ставит `decision = refused`: так переписка
показывает «лид в отказе», а передача в CRM и выгрузка лидов его не берут (`lead_handoff._leads`).

Проверки — те же, что у «взять» (`ReplyRepository.open_lead`): не лид, уже в работе или уже в
отказе — отказ словами до записи. Журнал пишет маршрут (`AuditAction.LEAD_REFUSED`).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.features.core.domain import ReplyDecision
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies.repository import ReplyRepository


async def refuse(
    repository: ReplyRepository, reply: ReplyModel, *, by: str, now: datetime | None = None
) -> datetime:
    """Лид — в отказ: кто и когда, и слово решения. Возвращает время решения."""
    await repository.open_lead(reply)
    moment = now or datetime.now(UTC)
    reply.reviewed_by = by[:128]
    reply.reviewed_at = moment
    reply.decision = ReplyDecision.REFUSED
    return moment
