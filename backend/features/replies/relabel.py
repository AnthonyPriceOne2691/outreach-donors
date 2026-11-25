"""Переразбор вида уже лежащих ответов нынешними правилами (`classify`) — 25.11.2026.

Правило разбора узнаёт автоответ только у нового письма: ответ, пришедший до правила, так и
остаётся «ответом человека» и ждёт его на экране. Проверка прода 25.11.2026: подтверждения служб
поддержки без номера заявки висели в «Диалогах с рекламодателями» как лиды. Команда
`replies-relabel` (`cli/replies_relabel.py`) прогоняет нынешний разбор по лежащим ответам и
показывает, а с `--yes` записывает, какие из них — автоответ.

**Что берётся:** ответ человека доноров и рекламодателей, по которому человек ещё не решал:
не взят, не разобран, не в отказе. Продажи — нет: их ответы ведут свой разбор и свой агент.
**Что меняется:** только вид ответа — на автоответ, и только по правилу, которое назвало его
автоответом. Цепочка при живом ответе уже остановлена (`ReplyRepository.stop_chain`) и сама не
возобновляется: службе поддержки добивки не нужны.

Заголовков письма в базе нет: переразбор судит по тексту, теме, адресу и файлам. Правила по
заголовкам здесь молчат, и автоответом ответ становится только по словам.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.replies.classify import classify
from backend.features.replies.inbound import Attachment, Incoming

#: Этапы, чьи ответы переразбираются: продажи ведут свои ответы сами.
STAGES = (Stage.DONORS, Stage.ADVERTISERS)


@dataclass(frozen=True, slots=True)
class Relabel:
    """Ответ, который нынешний разбор называет автоответом, и правило, которое так решило."""

    reply_id: int
    stage: Stage
    rule: str
    subject: str


async def candidates(session: AsyncSession) -> list[Relabel]:
    """Лежащие ответы человека доноров и рекламодателей, ещё ждущие решения, которые нынешний
    разбор называет автоответом. Ничего не пишет."""
    rows = (
        await session.execute(
            select(ReplyModel, CampaignModel.stage)
            .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(
                ReplyModel.kind == ReplyKind.HUMAN,
                ReplyModel.reviewed_at.is_(None),
                ReplyModel.decision.is_(None),
                CampaignModel.stage.in_(STAGES),
            )
            .order_by(ReplyModel.id)
        )
    ).all()
    files = await _files(session, [reply.id for reply, _ in rows])
    found: list[Relabel] = []
    for reply, stage in rows:
        verdict = classify(_incoming(reply, files.get(reply.id, ())))
        if verdict.kind is ReplyKind.AUTO_REPLY and verdict.rule is not None:
            found.append(Relabel(reply.id, stage, verdict.rule, reply.subject or ""))
    return found


async def apply(session: AsyncSession, found: Sequence[Relabel]) -> int:
    """Записать автоответ найденным ответам — одним запросом с теми же условиями, что у показа:
    между показом и записью человек мог взять ответ в работу или отправить в отказ, и такой ответ
    не трогается (ревью продаж 25.11.2026). Ответ — сколько записано. Коммит — за вызывающим."""
    if not found:
        return 0
    result = await session.execute(
        update(ReplyModel)
        .where(
            ReplyModel.id.in_([item.reply_id for item in found]),
            ReplyModel.kind == ReplyKind.HUMAN,
            ReplyModel.reviewed_at.is_(None),
            ReplyModel.decision.is_(None),
        )
        .values(kind=ReplyKind.AUTO_REPLY)
    )
    return int(cast("CursorResult[Any]", result).rowcount)


async def _files(
    session: AsyncSession, reply_ids: Sequence[int]
) -> dict[int, tuple[Attachment, ...]]:
    """Вложения ответов — без самих файлов: разбору нужны имя и размер."""
    if not reply_ids:
        return {}
    rows = await session.execute(
        select(
            ReplyAttachmentModel.reply_id,
            ReplyAttachmentModel.name,
            ReplyAttachmentModel.size,
            ReplyAttachmentModel.content_type,
        ).where(ReplyAttachmentModel.reply_id.in_(reply_ids))
    )
    found: dict[int, tuple[Attachment, ...]] = {}
    for reply_id, name, size, content_type in rows.all():
        found[reply_id] = (*found.get(reply_id, ()), Attachment(name, size, content_type))
    return found


def _incoming(reply: ReplyModel, files: tuple[Attachment, ...]) -> Incoming:
    """Письмо, каким его видел разбор, — без заголовков: их в базе нет."""
    return Incoming(
        message_id=reply.inbound_message_id or f"<reply-{reply.id}>",
        to=tuple(reply.to_addresses or ()),
        from_email=reply.from_email or "",
        subject=reply.subject or "",
        text=reply.raw_body,
        attachments=files,
        from_name=reply.from_name or "",
    )
