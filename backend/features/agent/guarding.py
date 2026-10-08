"""Судья черновика и петля правки — общие для всех этапов.

Судья этапа (`AgentStage.guard`) получает черновик, письмо собеседника и
факты брифа и выносит `Verdict`:

- `allow` — черновик готов (`drafted`);
- `block` — нарушения словами уходят писателю, и он переписывает черновик;
  так — до `max_rewrites` правок, а не сошлось — черновик отдан человеку
  (`escalated`) с историей попыток в `meta["attempts"]`;
- `escalate` — человеку сразу.

**Отказ закрыт.** Исключение судьи, его таймаут или `block` без причины —
это `escalate`, никогда не `allow`: судья, пропускающий при ошибке,
пропускает ровно тогда, когда проверить не смог.

Писатель сам сомневается или не вернул текста — судья не зовётся: ответ уже
у человека. Судьи у этапа нет — черновик писателя как есть, как до шва.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.agent.stages import (
    AGENT_STAGES,
    AgentStage,
    GuardInput,
    Verdict,
    VerdictKind,
    agent_operations,
)
from backend.features.agent.writer import Request, Written
from backend.features.core import usage
from backend.features.core.domain import Stage

logger = logging.getLogger(__name__)


class Writer(Protocol):
    async def write(self, request: Request) -> Written: ...


@dataclass(frozen=True, slots=True)
class Composed:
    """Последний черновик петли и отдан ли он человеку — с причиной."""

    body: str
    held: bool
    reason: str | None
    tokens: int
    attempts: list[dict[str, Any]] = field(default_factory=list)


async def compose(
    session: AsyncSession,
    stage: AgentStage,
    writer: Writer,
    request: Request,
    *,
    incoming: str,
) -> Composed:
    """Черновик, проверенный судьёй этапа, — с правками до предела."""
    attempts: list[dict[str, Any]] = []
    tokens = 0
    current = request
    cap = drafts_cap()
    for attempt in range(max(stage.max_rewrites, 0) + 1):
        await usage.ensure_llm_within_cap(session, own=cap)
        written = await writer.write(current)
        tokens += _spent(session, stage.usage_operation, written.tokens)
        held = written.needs_human or not written.body.strip()
        if held or stage.guard is None:
            return Composed(written.body, held, written.reason, tokens, attempts)
        check = GuardInput(
            stage=request.stage,
            draft=written.body,
            incoming=incoming,
            facts=request.facts,
            settings=request.settings,
            attempt=attempt,
        )
        verdict = await judged(stage, check)
        tokens += _spent(session, stage.guard_operation or stage.usage_operation, verdict.tokens)
        attempts.append(
            {"attempt": attempt, "verdict": verdict.kind.value, "reasons": list(verdict.reasons)}
        )
        if verdict.kind is VerdictKind.ALLOW:
            return Composed(written.body, False, None, tokens, attempts)
        if verdict.kind is VerdictKind.ESCALATE:
            return Composed(written.body, True, _said(verdict), tokens, attempts)
        current = replace(request, corrections=verdict.reasons, previous=written.body)
    why = f"судья не пропустил черновик и после {stage.max_rewrites} правок: {_said(verdict)}"
    return Composed(written.body, True, why, tokens, attempts)


def drafts_cap() -> usage.OwnCap:
    """Свой дневной потолок черновиков агента: настройка, не задана — доля общего.
    Общий 0 («потолка нет») при не заданном своём — потолка нет и у черновиков."""
    own = usage.share_cap(llm_cfg.AGENT_DAILY_TOKEN_CAP, llm_cfg.AGENT_CAP_SHARE)
    return usage.OwnCap("черновиков агента", agent_operations(), own, "AGENT_DAILY_TOKEN_CAP")


def said_at_start() -> list[str]:
    """Что процесс, который пишет черновики агента и тратит модель, говорит при старте.

    Словами и с именами настроек: на каких этапах агент ведёт переписку и включён ли агент
    продаж — в журнал `info`; потолок, который черновиков не держит, — `warning`. Без этой
    строки и выключенный агент продаж, и «потолка нет ни у чего» на сервере видно только по
    коду и `.env`. Возвращает сказанное.
    """
    general, own = llm_cfg.DAILY_TOKEN_CAP, llm_cfg.AGENT_DAILY_TOKEN_CAP
    registry = _registry_said()
    logger.info("%s", registry, extra={"stages": [stage.value for stage in AGENT_STAGES]})
    cap = _cap_said(general, own)
    if cap is None:
        return [registry]
    logger.warning("%s", cap, extra={"general_cap": general, "own_cap": own})
    return [registry, cap]


def _registry_said() -> str:
    stages = ", ".join(f"«{stage.value}»" for stage in AGENT_STAGES)
    sales = "включён" if Stage.SALES in AGENT_STAGES else "выключен"
    return f"агент переписки ведёт этапы {stages}; агент продаж {sales} (SALES_AGENT_ENABLED)"


def _cap_said(general: int, own: int | None) -> str | None:
    """Потолок, который черновиков не держит: общего нет, своего нет или он выше доли.
    `None` — черновики в своём потолке не больше доли общего."""
    if not general:
        spent = (
            "судья и разбор ответов тратят без предела, черновики агента — до своего потолка "
            f"{own} (AGENT_DAILY_TOKEN_CAP)"
            if own
            else "черновики агента, судья и разбор ответов тратят без предела"
        )
        return f"потолка расхода на модель за день нет (LLM_DAILY_TOKEN_CAP=0): {spent}"
    if own == 0:
        return (
            "своего потолка черновиков нет (AGENT_DAILY_TOKEN_CAP=0): черновики могут выбрать "
            f"весь общий {general} (LLM_DAILY_TOKEN_CAP) — разбору ответов и судье не останется"
        )
    if own is None or own <= usage.share_cap(None, llm_cfg.AGENT_CAP_SHARE):
        return None
    named = f"свой потолок черновиков {own} (AGENT_DAILY_TOKEN_CAP)"
    if own > general:
        return f"{named} больше общего {general} (LLM_DAILY_TOKEN_CAP) — он ничего не ограничивает"
    return (
        f"{named} больше доли {round(llm_cfg.AGENT_CAP_SHARE * 100)} % общего {general} "
        "(LLM_DAILY_TOKEN_CAP): черновики могут выбрать день разбору ответов и судье"
    )


async def judged(stage: AgentStage, check: GuardInput) -> Verdict:
    """Решение судьи — с закрытым отказом: сбой и молчание судьи — человеку."""
    assert stage.guard is not None
    try:
        async with asyncio.timeout(stage.guard_timeout_s):
            verdict = await stage.guard(check)
    except TimeoutError:
        logger.warning("агент: судья не ответил за %s с — черновик человеку", stage.guard_timeout_s)
        return Verdict(VerdictKind.ESCALATE, (f"судья не ответил за {stage.guard_timeout_s:g} с",))
    except Exception as exc:
        logger.exception("агент: судья упал — черновик человеку")
        return Verdict(VerdictKind.ESCALATE, (f"судья не смог проверить: {type(exc).__name__}",))
    if verdict.kind is VerdictKind.BLOCK and not verdict.reasons:
        return replace(verdict, kind=VerdictKind.ESCALATE, reasons=("судья вернул без причины",))
    return verdict


def _said(verdict: Verdict) -> str:
    return "судья: " + "; ".join(verdict.reasons or ("без причины",))


def _spent(session: AsyncSession, operation: str, tokens: int) -> int:
    if tokens:
        usage.record(session, operation=operation, units=tokens)
    return tokens
