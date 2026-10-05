"""Что уходит и приходит по маршрутам настроек агента переписки."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from backend.features.agent.settings import AgentSettings, settings_of
from backend.features.core.domain import Stage
from backend.features.core.models.agent import AgentSettingsModel

#: Пункт списка — довод или тема. Длиннее — это уже абзац, а не пункт.
Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]

#: Пунктов в списке. Агент ведёт доводы по порядку, и три десятка в одном
#: разговоре — это анкета, а не письмо.
MAX_LINES = 20

#: Предел цены, долларов. Выше — почти наверняка лишний ноль.
MAX_PRICE = Decimal("100000")


class AgentSettingsBody(BaseModel):
    """Настройки агента одного этапа — то, что правит человек."""

    enabled: bool
    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    tone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    points: list[Line] = Field(max_length=MAX_LINES)
    price_limit_usd: Decimal | None = Field(default=None, ge=0, le=MAX_PRICE, decimal_places=2)
    stop_topics: list[Line] = Field(max_length=MAX_LINES)

    @field_validator("points", "stop_topics")
    @classmethod
    def _without_blank(cls, lines: list[str]) -> list[str]:
        """Пустые строки — след переносов в поле ввода, а не пункты."""
        return [line for line in lines if line]

    def to_settings(self) -> AgentSettings:
        return AgentSettings(
            enabled=self.enabled,
            goal=self.goal,
            tone=self.tone,
            points=tuple(self.points),
            price_limit_usd=self.price_limit_usd,
            stop_topics=tuple(self.stop_topics),
        )

    @classmethod
    def of(cls, settings: AgentSettings) -> AgentSettingsBody:
        return cls(
            enabled=settings.enabled,
            goal=settings.goal,
            tone=settings.tone,
            points=list(settings.points),
            price_limit_usd=settings.price_limit_usd,
            stop_topics=list(settings.stop_topics),
        )


class AgentSettingsVersion(BaseModel):
    """Версия настроек: что стояло, кто поставил и когда."""

    version: int
    created_by: str | None
    created_at: datetime
    settings: AgentSettingsBody

    @classmethod
    def of(cls, row: AgentSettingsModel) -> AgentSettingsVersion:
        return cls(
            version=row.version,
            created_by=row.created_by,
            created_at=row.created_at,
            settings=AgentSettingsBody.of(settings_of(row)),
        )


class AgentStageView(BaseModel):
    """Настройки этапа: текущая версия, с чего начать и последние правки.

    `current` пусто — этап не настраивали, и агент на нём не пишет.
    """

    stage: Stage
    current: AgentSettingsVersion | None
    defaults: AgentSettingsBody
    history: list[AgentSettingsVersion]


class AgentView(BaseModel):
    """Оба этапа одним ответом: экран показывает их рядом."""

    stages: list[AgentStageView]
