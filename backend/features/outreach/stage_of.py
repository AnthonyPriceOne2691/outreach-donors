"""Этап строки почты по её номеру — проверке права «Продажи» (решение Anthony 10.10.2026, П2).

Маршрут письма, переписки, ответа или черновика агента — а с П2б и ящика рассылки и записи
стоп-листа — спрашивает здесь этап строки до своего тела: строка продаж без права «Продажи» —
отказ словами (`access.permissions.require_stage`). Строки нет — `None`, и «не найдено» говорит сам
маршрут, как до этой проверки; номер больше столбца — тоже `None`, а не отказ базы
(`shared/database/ids.py`).

Из базы — только этап, коротким запросом: тексты писем и ответов проверке не нужны. У ответа
без письма — его адреса и наши домены рассылки (`replies/recipient_stage.py`).
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.replies import recipient_stage
from backend.shared.database.ids import storable


async def of_message(session: AsyncSession, message_id: int) -> Stage | None:
    """Этап письма — по его рассылке."""
    query = (
        select(CampaignModel.stage)
        .join(MessageModel, MessageModel.campaign_id == CampaignModel.id)
        .where(MessageModel.id == message_id)
    )
    return await _stage(session, message_id, query)


async def of_thread(session: AsyncSession, thread_id: int) -> Stage | None:
    """Этап переписки — по рассылке, которая её начала."""
    query = (
        select(CampaignModel.stage)
        .join(ThreadModel, ThreadModel.campaign_id == CampaignModel.id)
        .where(ThreadModel.id == thread_id)
    )
    return await _stage(session, thread_id, query)


async def of_reply(session: AsyncSession, reply_id: int) -> Stage | None:
    """Этап ответа — по переписке, а без неё по письму, на которое ответили: порядок тот же,
    что у `ReplyRepository.stage_of`, — переписка переживает удаление письма. Ответ без
    письма и переписки (`replies/unbound.py`) — по нашему адресу, на который он пришёл
    (`replies/recipient_stage.py`): ответ на ящик продаж открывается только с правом «Продажи»,
    как в списке «Не привязаны»; нашего адреса нет — этапа нет."""
    if not storable(reply_id):
        return None
    row = (
        await session.execute(
            select(ReplyModel.thread_id, ReplyModel.message_id, ReplyModel.to_addresses).where(
                ReplyModel.id == reply_id
            )
        )
    ).first()
    if row is None:
        return None
    thread_id, message_id, addresses = row
    if thread_id is not None:
        return await of_thread(session, thread_id)
    if message_id is not None:
        return await of_message(session, message_id)
    return await recipient_stage.of_addresses(session, addresses)


async def of_draft(session: AsyncSession, draft_id: int) -> Stage | None:
    """Этап черновика агента — по версии настроек, которой он написан: её берут по этапу
    переписки (`agent/drafting.py`), и тем же полем сужается список черновиков."""
    query = (
        select(AgentSettingsModel.stage)
        .join(AgentDraftModel, AgentDraftModel.settings_id == AgentSettingsModel.id)
        .where(AgentDraftModel.id == draft_id)
    )
    return await _stage(session, draft_id, query)


async def of_sender(session: AsyncSession, sender_id: int) -> Stage | None:
    """Этап ящика рассылки — его направление (П2б)."""
    query = select(SenderModel.stage).where(SenderModel.id == sender_id)
    return await _stage(session, sender_id, query)


async def of_suppression(session: AsyncSession, row_id: int) -> Stage | None:
    """Этап записи стоп-листа (П2б). Запись без этапа держит все этапы — `None`: её снимает
    своё право маршрута, как и до П2б."""
    query = select(SuppressionModel.stage).where(SuppressionModel.id == row_id)
    return await _stage(session, row_id, query)


async def _stage(
    session: AsyncSession, number: int, query: Select[tuple[Stage]] | Select[tuple[Stage | None]]
) -> Stage | None:
    """Этап из запроса по номеру строки; номер, которого быть не может, — без запроса.
    Этап у записи стоп-листа необязателен — отсюда второй вид запроса."""
    if not storable(number):
        return None
    found: Stage | None = await session.scalar(query)
    return found
