"""Этап агента переписки: что у этапа своё, а что — общий механизм.

Механизм один на все этапы: кому черновик положен, запись, петля правки,
решения человека, автопилот. Этап даёт свои части — с чего начать настройки,
на чьей он стороне цены, промпт, модель и операцию расхода, бриф до письма,
судью, крючок уведомления и предел правок. Новый этап — строка реестра
`AGENT_STAGES`, а не ветка `if stage is …` в механизме.

**Бриф — до письма** (`brief`): этап смотрит на переписку и говорит, что агенту
знать (факты строками), не писать ли вовсе (skip двух видов), что запомнить
для калибровки (`meta`, ложится в черновик) и чьим именем подписать черновик
(`sign_as`; не назвал — общим именем отправителя). `no_reply` — ответ не нужен,
черновик `skipped`; `human` — агент не берётся, черновик `escalated` без
текста: модель в обоих случаях не зовётся.

**Судья — после письма** (`guard`): получает черновик, письмо собеседника и
факты брифа и выносит `Verdict` — пропустить, вернуть на правку с причинами
словами или отдать человеку. Судьи нет — черновик не проверяется сверх того,
что проверяет писатель (`writer.checked`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.agent.settings import AgentSettings, UnknownAgentStageError, defaults
from backend.features.agent.writer import PROMPT_PATH, PROMPT_VERSION, Turn
from backend.features.core.domain import DraftStatus, Stage


class PriceSide(StrEnum):
    """На чьей стороне цены этап: предел цены — потолок или пол."""

    #: Мы покупаем: дороже предела не соглашаемся (доноры).
    BUY = "buy"
    #: Мы продаём: дешевле предела не отдаём (рекламодатели).
    SELL = "sell"


class SkipKind(StrEnum):
    #: Ответ не нужен («спасибо, получил») — черновик `skipped`.
    NO_REPLY = "no_reply"
    #: Агент не берётся — черновик `escalated` без текста, решает человек.
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class Skip:
    kind: SkipKind
    #: Почему — словами: её видит человек.
    reason: str


@dataclass(frozen=True, slots=True)
class Conversation:
    """Что этап видит до письма: переписка после очистки и настройки."""

    stage: Stage
    thread_id: int
    reply_id: int
    turns: tuple[Turn, ...]
    settings: AgentSettings


@dataclass(frozen=True, slots=True)
class Brief:
    """Ответ этапа до письма: факты, пропуск с причиной, мета для калибровки."""

    facts: tuple[str, ...] = ()
    skip: Skip | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)
    #: Чьим именем подписать черновик. `None` — общим именем отправителя
    #: `OUTREACH_SENDER_NAME`, как до шва; пустое имя — отказ, а не письмо без подписи.
    sign_as: str | None = None

    def __post_init__(self) -> None:
        if self.sign_as is not None and not self.sign_as.strip():
            raise ValueError("пустое имя подписи — задай имя или None")


class VerdictKind(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Решение судьи. `reasons` — словами: при `block` они уходят писателю."""

    kind: VerdictKind
    reasons: tuple[str, ...] = ()
    #: Сколько судья потратил токенов — пишется в расход этапа.
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class GuardInput:
    """Что судья видит: черновик, письмо собеседника, факты брифа."""

    stage: Stage
    draft: str
    incoming: str
    facts: tuple[str, ...]
    settings: AgentSettings
    #: 0 — первый черновик, дальше — номер правки.
    attempt: int


@dataclass(frozen=True, slots=True)
class DraftNotice:
    """Черновик записан — что о нём знает крючок уведомления этапа."""

    draft_id: int
    reply_id: int
    thread_id: int
    stage: Stage
    status: DraftStatus
    reason: str | None


BriefHook = Callable[[AsyncSession, Conversation], Awaitable[Brief]]
Guard = Callable[[GuardInput], Awaitable[Verdict]]
OnDraft = Callable[[DraftNotice], Awaitable[None]]


async def no_brief(_session: AsyncSession, _conversation: Conversation) -> Brief:
    """Бриф этапа, которому до письма нечего добавить: пишем по настройкам."""
    return Brief()


@dataclass(frozen=True, slots=True)
class AgentStage:
    """Части агента, свои у этапа. Всё, кроме умолчаний и стороны цены, —
    с умолчанием «как у первых двух этапов»."""

    defaults: AgentSettings
    price: PriceSide
    prompt: Path = PROMPT_PATH
    prompt_version: str = PROMPT_VERSION
    model: str = llm_cfg.AGENT_MODEL
    usage_operation: str = "agent_draft"
    #: Может ли этап отвечать без человека. Флаг кода, а не настройка экрана:
    #: включить автопилот этапу — решение, которое подписывает владелец.
    autopilot: bool = False
    brief: BriefHook = no_brief
    guard: Guard | None = None
    #: Операция расхода судьи; `None` — расход судьи идёт операцией черновика.
    guard_operation: str | None = None
    #: Сколько секунд ждать судью: дольше — `escalate`, как при его ошибке.
    guard_timeout_s: float = 60.0
    on_draft: OnDraft | None = None
    #: Сколько раз переписать черновик по замечаниям судьи, прежде чем отдать
    #: человеку.
    max_rewrites: int = 3


#: Этапы, на которых агент ведёт переписку. Явным реестром, а не всем
#: `Stage`: новый этап не получает агента молча — без умолчаний и стороны
#: цены экран упал бы на первом чтении, а агент писал бы чужим промптом.
AGENT_STAGES: Mapping[Stage, AgentStage] = MappingProxyType(
    {
        Stage.DONORS: AgentStage(defaults=defaults(Stage.DONORS), price=PriceSide.BUY),
        Stage.ADVERTISERS: AgentStage(defaults=defaults(Stage.ADVERTISERS), price=PriceSide.SELL),
    }
)


def agent_stage(stage: Stage) -> AgentStage:
    """Части агента этапа. Этапа нет в реестре — отказ словами."""
    found = AGENT_STAGES.get(stage)
    if found is None:
        raise UnknownAgentStageError(f"На этапе «{stage.value}» агент переписку не ведёт")
    return found
