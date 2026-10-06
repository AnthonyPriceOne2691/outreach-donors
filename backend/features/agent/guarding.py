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

from backend.features.agent.stages import AgentStage, GuardInput, Verdict, VerdictKind
from backend.features.agent.writer import Request, Written
from backend.features.core import usage

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
    for attempt in range(max(stage.max_rewrites, 0) + 1):
        await usage.ensure_llm_within_cap(session)
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
