"""Что уходит и приходит по маршрутам настроек агента переписки."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from backend.features.agent.drafts import ShownDraft
from backend.features.agent.settings import AgentSettings, settings_of
from backend.features.agent.stages import PriceSide
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
    #: `drafts` — черновик отправляет человек, `autopilot` — ответ в границах уходит
    #: сам (включают там, где разрешено, а версию в нём сохраняет тот, у кого send).
    #: Не прислан — остаётся режим текущей версии: экран без поля не выключит автопилот.
    mode: Literal["drafts", "autopilot"] | None = None
    #: Ответов автопилота в переписке до передачи человеку; не прислан — текущий.
    max_turns: int | None = Field(default=None, ge=1, le=10)

    @field_validator("points", "stop_topics")
    @classmethod
    def _without_blank(cls, lines: list[str]) -> list[str]:
        """Пустые строки — след переносов в поле ввода, а не пункты."""
        return [line for line in lines if line]

    @field_validator("mode", "max_turns")
    @classmethod
    def _not_null(cls, value: object) -> object:
        """Явный `null` — ошибка: чтобы оставить как было, поле не присылают."""
        if value is None:
            raise ValueError("пусто — не присылайте поле, и останется текущее значение")
        return value

    def to_settings(
        self, current: AgentSettingsModel | None, defaults: AgentSettings
    ) -> AgentSettings:
        """Настройки из тела. Режим и предел, которых тело не прислало (их нет в
        `model_fields_set`), — из текущей версии этапа, у не настроенного — умолчания."""
        kept = defaults if current is None else settings_of(current)
        sent = self.model_fields_set
        mode = self.mode if "mode" in sent and self.mode else kept.mode
        turns = self.max_turns if "max_turns" in sent and self.max_turns else kept.max_turns
        return AgentSettings(
            enabled=self.enabled,
            goal=self.goal,
            tone=self.tone,
            points=tuple(self.points),
            price_limit_usd=self.price_limit_usd,
            stop_topics=tuple(self.stop_topics),
            mode=mode,
            max_turns=turns,
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
    #: Кому агент пишет на этапе — имя в переключателе экрана — и что он там
    #: делает: из реестра, чтобы новый этап встал на экран без его правки.
    title: str
    lead: str
    #: На чьей стороне цены этап: предел — «не дороже» (`buy`) или «не дешевле».
    price_side: PriceSide
    current: AgentSettingsVersion | None
    defaults: AgentSettingsBody
    history: list[AgentSettingsVersion]
    #: Разрешён ли этапу автопилот (код этапа и выключатель сервера): нет —
    #: экран переключателя не показывает, а сервер режим не сохранит.
    autopilot_allowed: bool = False
    #: Почему не разрешён — словами `autopilot.refusal`: автопилот, выбранный
    #: раньше, при снятом выключателе писем не шлёт, и экран говорит почему.
    autopilot_refusal: str | None = None


class AgentView(BaseModel):
    """Все этапы реестра одним ответом, в его порядке: экран показывает их рядом."""

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
    #: Последний вердикт судьи этапа (`allow`/`block`/`escalate`) и сколько раз
    #: писатель писал под его проверкой; судьи не было — `None` и 0.
    verdict: str | None = None
    attempts: int = 0

    @classmethod
    def of(cls, shown: ShownDraft) -> DraftCard:
        draft = shown.draft
        verdict, attempts = _judged(draft.meta)
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
            verdict=verdict,
            attempts=attempts,
        )


def _judged(meta: dict[str, Any]) -> tuple[str | None, int]:
    """Последний вердикт судьи и число попыток — из `meta["attempts"]`
    (`agent/guarding.compose`). Там и бриф этапа, так что форму не берём на веру."""
    tried = meta.get("attempts")
    attempts = [one for one in tried if isinstance(one, dict)] if isinstance(tried, list) else []
    last = attempts[-1].get("verdict") if attempts else None
    return (last if isinstance(last, str) else None), len(attempts)


class DraftDetail(DraftCard):
    """Черновик целиком: что знал этап (`meta`), что ушло и почему отклонён."""

    meta: dict[str, Any]
    model: str
    prompt_version: str
    tokens: int
    final_body: str | None
    edited: bool | None
    reject_reason: str | None
    reject_kind: str | None
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
            reject_kind=draft.reject_kind,
            sent_message_id=draft.sent_message_id,
        )


class SendDraftBody(BaseModel):
    """`body` пусто — отправить как есть; иначе — текст с правкой."""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None


class RejectDraftBody(BaseModel):
    """Отклонить можно только с причиной: без неё — 422 словами.

    У этапа со строгим списком (`AgentStage.strict_reasons`, продажи) причина —
    пункт списка или «другое: …» словами; иначе 422 словами со списком. Проверяет
    ядро (`agent/drafts.reject_draft`): правило одно и для экрана, и для API.
    """

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
