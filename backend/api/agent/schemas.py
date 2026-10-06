"""Что уходит и приходит по маршрутам настроек агента переписки."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from backend.features.agent.drafts import ShownDraft
from backend.features.agent.settings import AgentSettings, settings_of
from backend.features.core.domain import DraftStatus, Stage
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
    #: `drafts` — черновик отправляет человек, `autopilot` — ответ в границах
    #: уходит сам. Автопилот сохраняется, только если этапу он разрешён (код
    #: этапа и выключатель сервера) и у сохраняющего есть право send.
    mode: Literal["drafts", "autopilot"] = "drafts"
    #: Ответов автопилота в одной переписке, прежде чем она уйдёт человеку.
    max_turns: int = Field(default=2, ge=1, le=10)

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
            mode=self.mode,
            max_turns=self.max_turns,
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
            mode="autopilot" if settings.mode == "autopilot" else "drafts",
            max_turns=settings.max_turns,
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
    #: Разрешён ли этапу автопилот (код этапа и выключатель сервера): нет —
    #: экран переключателя не показывает, а сервер режим не сохранит.
    autopilot_allowed: bool = False


class AgentView(BaseModel):
    """Оба этапа одним ответом: экран показывает их рядом."""

    stages: list[AgentStageView]


class DraftCard(BaseModel):
    """Черновик агента под ответом собеседника.

    `status`: `drafted` — готов, `escalated` — отдан человеку (как есть не
    уходит), `skipped` — ответ не нужен, `sent` / `rejected` — решение принято.
    `reason` — почему отдан человеку или пропущен. Пустой `body` — текста нет.
    """

    id: int
    reply_id: int
    thread_id: int | None
    status: DraftStatus
    body: str
    reason: str | None
    settings_version: int
    written_at: datetime
    decided_by: str | None
    decided_at: datetime | None

    @classmethod
    def of(cls, shown: ShownDraft) -> DraftCard:
        draft = shown.draft
        return cls(
            id=draft.id,
            reply_id=draft.reply_id,
            thread_id=shown.thread_id,
            status=draft.status,
            body=draft.body,
            reason=draft.reason,
            settings_version=shown.settings_version,
            written_at=draft.updated_at,
            decided_by=draft.decided_by,
            decided_at=draft.decided_at,
        )


class DraftDetail(DraftCard):
    """Черновик целиком: что знал этап (`meta`), что ушло и почему отклонён."""

    meta: dict[str, Any]
    model: str
    prompt_version: str
    tokens: int
    final_body: str | None
    edited: bool | None
    reject_reason: str | None
    sent_message_id: int | None

    @classmethod
    def of(cls, shown: ShownDraft) -> DraftDetail:
        draft = shown.draft
        return cls(
            **DraftCard.of(shown).model_dump(),
            meta=draft.meta,
            model=draft.model,
            prompt_version=draft.prompt_version,
            tokens=draft.tokens,
            final_body=draft.final_body,
            edited=draft.edited,
            reject_reason=draft.reject_reason,
            sent_message_id=draft.sent_message_id,
        )


class SendDraftBody(BaseModel):
    """`body` пусто — отправить как есть; иначе — текст с правкой."""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None


class RejectDraftBody(BaseModel):
    """Отклонить можно только с причиной: без неё — 422 словами."""

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
