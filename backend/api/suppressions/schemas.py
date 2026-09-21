"""Стоп-лист: что уходит на экран и что приходит с него."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.features.core.domain import Stage, SuppressionReason
from backend.features.letters.stoplist import StopRow


class StopEntry(BaseModel):
    """Строка списка. Домен или адрес — ровно одно из двух."""

    id: int
    host: str | None
    email: str | None
    reason: SuppressionReason
    stage: Stage | None
    created_by: str | None
    created_at: datetime
    #: Решение адресата, а не наше: снимается только с причиной.
    #: Считает сервер — второй экземпляр правила на фронте разошёлся бы
    #: с настоящим на первой новой причине.
    donor_decision: bool

    @classmethod
    def of(cls, row: StopRow) -> StopEntry:
        return cls(
            id=row.id,
            host=row.host,
            email=row.email,
            reason=row.reason,
            stage=row.stage,
            created_by=row.created_by,
            created_at=row.created_at,
            donor_decision=row.donor_decision,
        )


class StopListView(BaseModel):
    """Весь список и его состав по причинам — числа для шапки экрана."""

    rows: list[StopEntry]
    total: int
    donor_decisions: int


class AddBody(BaseModel):
    """Завести запись руками."""

    target: str = Field(min_length=3, max_length=253)
    reason: SuppressionReason
    #: Пусто — запрет действует на обоих этапах.
    stage: Stage | None = None


class RemoveBody(BaseModel):
    """Снять запись. Причина обязательна для решения адресата."""

    reason: str | None = Field(default=None, max_length=500)
