"""Что уходит по маршруту журнала действий."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from backend.features.access.journal import JournalEntry, JournalPage
from backend.features.core.domain import AuditAction


class JournalEntryCard(BaseModel):
    """Запись журнала: когда, кто, что и над чем. Действие и цель — кодом: слова
    у экрана (`frontend/src/api/auditLabels.ts`), по кодам журнал разбирают."""

    id: int
    created_at: datetime
    action: AuditAction
    #: Кто. Пусто — автора нет: неудачный вход с чужой почтой, загрузка из консоли
    #: (кто — в подробностях, ключом «кто»).
    author_id: int | None
    author_email: str | None
    #: Над чем: «run:27», «donor:12», «user:3».
    target: str | None
    #: Подробности как записаны — ключи словами, значения как были.
    details: dict[str, Any] | None

    @classmethod
    def of(cls, entry: JournalEntry) -> JournalEntryCard:
        return cls(
            id=entry.id,
            created_at=entry.created_at,
            action=entry.action,
            author_id=entry.author_id,
            author_email=entry.author_email,
            target=entry.target,
            details=entry.details,
        )


class JournalView(BaseModel):
    """Страница журнала. Размер страницы называет сервер — своей копии у экрана нет."""

    entries: list[JournalEntryCard]
    #: Сколько записей под фильтром всего — по нему экран считает страницы.
    total: int
    page: int
    limit: int
    #: Действия этапа продаж не показаны: у учётки нет права «Продажи» (П2). Экран
    #: говорит это словами — иначе журнал без них читался бы как весь.
    sales_hidden: bool

    @classmethod
    def of(cls, found: JournalPage, *, page: int, limit: int, sales_hidden: bool) -> JournalView:
        return cls(
            entries=[JournalEntryCard.of(entry) for entry in found.entries],
            total=found.total,
            page=page,
            limit=limit,
            sales_hidden=sales_hidden,
        )
