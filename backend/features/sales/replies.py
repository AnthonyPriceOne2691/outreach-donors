"""Ответ лида продаж: задача своей очереди (`sales`) берёт его после приёма.

Приём общий с донорами: вебхук, повтор, привязка, правила вида, последствия
(`replies/pipeline.py`). Ответ человека в треде продаж уходит сюда задачей
`queue.SALES_REPLY_JOB` — своей очередью и своим воркером, а не очередью
прогонов: часовой прогон доноров держал бы ответ лида до часа.

**Задача не верит, что её поставили по делу.** Задача живёт в очереди дольше
кода и может прийти ко второму ответу, к удалённому, к уже разобранному или
решённому человеком: каждый такой случай — итог словами, а не работа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import outcome
from backend.features.replies.repository import ReplyRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Handled:
    """Чем кончилась задача одного ответа."""

    reply_id: int
    #: Почему задача ответ не взяла — словами. Пусто — взяла.
    skipped: str | None = None
    #: Ждёт ли ответ человека и почему.
    waits: bool = False
    reason: str | None = None

    @property
    def as_report(self) -> dict[str, object]:
        """Итог задачи для очереди и журнала."""
        report: dict[str, object] = {"reply": self.reply_id}
        if self.skipped is not None:
            report["skipped"] = self.skipped
        else:
            report |= {"waits": self.waits, "reason": self.reason}
        return report


class SalesReplies:
    """Ответы продаж, взятые задачей очереди."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ReplyRepository(session)

    async def handle(self, reply_id: int) -> Handled:
        """Взять ответ. Вид ответа разбирает следующий шаг; пока ответ ждёт его."""
        reply = await self._session.get(ReplyModel, reply_id)
        if reply is None:
            logger.warning("продажи: ответа нет — задаче нечего делать", extra={"reply": reply_id})
            return Handled(reply_id, skipped="ответа нет: удалён до разбора")
        skipped = await self._not_ours(reply)
        if skipped is not None:
            logger.info("продажи: ответ не взят", extra={"reply": reply_id, "why": skipped})
            return Handled(reply_id, skipped=skipped)
        return Handled(reply_id, waits=True, reason=outcome.SALES_WAITING)

    async def _not_ours(self, reply: ReplyModel) -> str | None:
        """Почему задаче этот ответ не брать. `None` — брать."""
        if reply.reviewed_at is not None:
            return "решён человеком"
        if reply.model_parse is not None:
            return "уже разобран"
        stage = await self._repo.stage_of(reply)
        if stage is not Stage.SALES:
            return "не ответ продаж"
        if not outcome.to_sales_queue(reply.kind, stage):
            return f"вид «{reply.kind.value}» решают правила приёма"
        return None
