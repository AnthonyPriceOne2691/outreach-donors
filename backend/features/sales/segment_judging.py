"""Проход судьи сегмента по кандидатам прогона: кэш, правила, главная, модель, режим, учёт.

**Кого судим** (`waiting_judge`): кандидатов прогона, которых ещё не судили, и тех, за кого
модель не ответила, пока человек их не решил: отказ модели — не вердикт, и следующий
проход судит домен снова.

**Кэш — таблица вердиктов**: свежий вердикт того же домена, гипотезы, версии промпта и
описания сегмента не пересуживается и токенов не стоит. Вердикт правила и модели ложится
туда же; ответа модели нет — строки нет.

**Партиями**: до каждой партии — потолок модели продаж (`usage.ensure_llm_within_cap` со
своим потолком продаж, как у остальных вызовов модели продаж вне агента); партия — одна
транзакция: вердикты, полосы кандидатов и строка расхода за её токены. Потолок выбран —
проход останавливается словами, неосуждённые ждут следующего прохода.

**Главная — только тем, кого судит модель**: платформу и госзону правило режет по имени
домена, их сайт не открывается вовсе; кэш и выключенный судья главной не просят.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import judge as judge_cfg
from backend.config.judge import JudgeMode
from backend.features.core import usage
from backend.features.core.models.domain import DomainModel
from backend.features.donors.home_signals import check_home
from backend.features.sales import usage_cap
from backend.features.sales.segment_judge import (
    JUDGE_OFF,
    OPERATION,
    PROMPT_VERSION,
    SegmentClient,
    SegmentVerdict,
    SiteText,
    by_host,
    decide,
    effective,
    segment_digest,
    site_text,
)
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentVerdictModel,
)

logger = logging.getLogger(__name__)

#: Кандидатов в партии: партия — одна транзакция и одна строка расхода.
BATCH = 20
#: Срок вердикта: чем сайт живёт, он меняет раз в годы — как у судьи площадки.
VERDICT_TTL_DAYS = 180

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel


@dataclass(slots=True)
class JudgeTally:
    """Что судья сделал за проход. Числа — в отчёт прогона."""

    judged: int = 0
    from_cache: int = 0
    by_rule: int = 0
    #: Модель не ответила: вердикта нет, домен — человеку и следующему проходу.
    unanswered: int = 0
    unanswered_reason: str | None = None
    #: Модель сказала «нет»: в `enforce` отсечены, в наблюдении — человеку с пометкой.
    would_cut: int = 0
    #: Упали мимо судьи (ошибка кода): остались неосуждёнными, следующий проход повторит.
    failed: int = 0
    tokens: int = 0
    #: Потолок модели выбран: почему проход остановлен.
    stopped: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "judged": self.judged,
            "from_cache": self.from_cache,
            "by_rule": self.by_rule,
            "unanswered": self.unanswered,
            "unanswered_reason": self.unanswered_reason,
            "would_cut": self.would_cut,
            "failed": self.failed,
            "tokens": self.tokens,
            "stopped": self.stopped,
        }


@dataclass(frozen=True, slots=True)
class Judged:
    """Вердикт по кандидату и текст, по которому он вынесен."""

    verdict: SegmentVerdict | None
    text: SiteText
    cached: bool = False


def waiting_judge() -> ColumnElement[bool]:
    """Кого судить: не судили — или модель не ответила, а человек ещё не решил."""
    return or_(
        Candidate.lane == Lane.PENDING,
        and_(Candidate.unanswered.is_(True), Candidate.decided_by.is_distinct_from(Decider.HUMAN)),
    )


async def _pending(session: AsyncSession, run_id: int) -> list[tuple[Candidate, str]]:
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.run_id == run_id, waiting_judge())
        .order_by(Candidate.id)
    )
    return list(rows.tuples().all())


async def cached(
    session: AsyncSession,
    *,
    hypothesis_id: int,
    digest: str,
    domain_ids: Sequence[int],
    now: datetime,
) -> dict[int, SegmentVerdict]:
    """Свежие вердикты той же версии промпта и того же описания сегмента — по домену."""
    if not domain_ids:
        return {}
    rows = await session.scalars(
        select(Verdict).where(
            Verdict.hypothesis_id == hypothesis_id,
            Verdict.prompt_version == PROMPT_VERSION,
            Verdict.segment_digest == digest,
            Verdict.domain_id.in_(list(domain_ids)),
            Verdict.updated_at >= now - timedelta(days=VERDICT_TTL_DAYS),
        )
    )
    return {
        row.domain_id: SegmentVerdict(
            Lane(row.verdict), row.reason, row.quote, row.model, 0, Decider(row.decided_by)
        )
        for row in rows.all()
    }


async def _one(
    candidate: Candidate,
    host: str,
    *,
    segment: str,
    client: SegmentClient | None,
    home: httpx.AsyncClient | None,
    known: SegmentVerdict | None,
) -> Judged:
    """Вердикт одному кандидату: правило по домену, кэш, главная, правила текста, модель.
    `client` пуст — судья выключен: правило по домену режет, остальное решает человек, и
    прежний вердикт модели из кэша не решает за него."""
    page = site_text(title=candidate.page_title, description=candidate.page_description)
    if (ruled := by_host(host)) is not None:
        return Judged(ruled, page)
    if client is None:
        return Judged(None, page)
    if known is not None:
        return Judged(known, page, cached=True)
    signals = await check_home(home, host) if home is not None else None
    text = site_text(
        title=candidate.page_title, description=candidate.page_description, home=signals
    )
    return Judged(await decide(client, host=host, segment=segment, text=text), text)


def _apply(candidate: Candidate, judged: Judged, mode: JudgeMode, tally: JudgeTally) -> None:
    """Вердикт и режим → полоса кандидата; счёт прохода."""
    verdict = judged.verdict
    candidate.site_text = judged.text.for_model() or None
    if verdict is None:  # судья выключен
        candidate.lane, candidate.reason, candidate.verdict = Lane.REVIEW, JUDGE_OFF, None
        candidate.decided_by, candidate.quote, candidate.unanswered = None, None, False
        return
    tally.tokens += verdict.tokens
    if verdict.unanswered:
        tally.unanswered += 1
        tally.unanswered_reason = tally.unanswered_reason or verdict.reason[:300]
        candidate.lane, candidate.reason, candidate.verdict = Lane.REVIEW, verdict.reason, None
        candidate.decided_by, candidate.quote, candidate.unanswered = None, None, True
        candidate.model = verdict.model
        return
    lane, reason = effective(verdict, mode)
    candidate.lane, candidate.reason, candidate.verdict = lane, reason, verdict.lane
    candidate.quote, candidate.decided_by, candidate.model = (
        verdict.quote,
        verdict.decided_by,
        verdict.model,
    )
    candidate.unanswered = False
    tally.from_cache += int(judged.cached)
    tally.judged += int(not judged.cached)
    tally.by_rule += int(verdict.decided_by is Decider.RULE)
    tally.would_cut += int(verdict.decided_by is Decider.MODEL and verdict.would_cut)


async def _remember(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Вердикты партии — в таблицу вердиктов: новый ключ — строка, тот же — поверх."""
    if not rows:
        return
    statement = insert(Verdict).values(list(rows))
    fresh = statement.excluded
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_sales_segment_verdicts_key",
            set_={
                "verdict": fresh.verdict,
                "quote": fresh.quote,
                "reason": fresh.reason,
                "model": fresh.model,
                "decided_by": fresh.decided_by,
                "tokens": fresh.tokens,
                "updated_at": fresh.updated_at,
            },
        )
    )


def _verdict_row(
    candidate: Candidate, verdict: SegmentVerdict, digest: str, moment: datetime
) -> dict[str, Any]:
    return {
        "domain_id": candidate.domain_id,
        "hypothesis_id": candidate.hypothesis_id,
        "prompt_version": PROMPT_VERSION,
        "segment_digest": digest,
        "verdict": verdict.lane.value,
        "quote": verdict.quote,
        "reason": verdict.reason[:1000],
        "model": verdict.model,
        "decided_by": verdict.decided_by.value,
        "tokens": verdict.tokens,
        "updated_at": moment,
    }


async def _batch(
    session: AsyncSession,
    batch: Sequence[tuple[Candidate, str]],
    *,
    segment: str,
    mode: JudgeMode,
    client: SegmentClient | None,
    home: httpx.AsyncClient | None,
    tally: JudgeTally,
    moment: datetime,
) -> None:
    """Одна партия: вердикты, полосы, таблица вердиктов и строка расхода — без коммита."""
    digest = segment_digest(segment)
    # Кэш — только когда судья зовётся: при выключенном каждый сайт решает человек.
    known = (
        await cached(
            session,
            hypothesis_id=batch[0][0].hypothesis_id,
            digest=digest,
            domain_ids=[candidate.domain_id for candidate, _ in batch],
            now=moment,
        )
        if client is not None
        else {}
    )
    gate = asyncio.Semaphore(judge_cfg.CONCURRENCY)

    async def one(candidate: Candidate, host: str) -> Judged:
        async with gate:
            return await _one(
                candidate,
                host,
                segment=segment,
                client=client,
                home=home,
                known=known.get(candidate.domain_id),
            )

    outcomes = await asyncio.gather(*(one(c, h) for c, h in batch), return_exceptions=True)
    remembered: list[dict[str, Any]] = []
    spent = tally.tokens
    for (candidate, host), outcome in zip(batch, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            # Судья не та ступень, ради которой стоит терять оплаченную выдачу: кандидат
            # остаётся неосуждённым, следующий проход повторит его.
            logger.warning("судья сегмента упал на %s: %r", host, outcome)
            tally.failed += 1
            continue
        _apply(candidate, outcome, mode, tally)
        verdict = outcome.verdict
        if verdict is not None and not verdict.unanswered and not outcome.cached:
            remembered.append(_verdict_row(candidate, verdict, digest, moment))
    await _remember(session, remembered)
    if tally.tokens > spent:
        usage.record(session, operation=OPERATION, units=tally.tokens - spent)


async def judge_run(
    session: AsyncSession,
    *,
    run_id: int,
    segment: str,
    mode: JudgeMode,
    client: SegmentClient | None,
    home: httpx.AsyncClient | None,
    now: datetime | None = None,
) -> JudgeTally:
    """Судить кандидатов прогона, которые ждут судью. Коммит — по партии, здесь.

    `mode` — режим прогона; выключенный судья (`off`) модели не зовёт: правило по домену
    режет, остальных решает человек. Потолок модели выбран — `stopped` словами, остальные
    ждут следующего прохода."""
    moment = now or datetime.now(UTC)
    tally = JudgeTally()
    waiting = await _pending(session, run_id)
    asked = None if mode is JudgeMode.OFF else client
    for start in range(0, len(waiting), BATCH):
        if asked is not None:
            try:
                await usage.ensure_llm_within_cap(session, now=now, own=usage_cap.sales_cap())
            except usage.LlmCapExceededError as exc:
                logger.warning("судья сегмента остановлен потолком модели", extra={"run": run_id})
                tally.stopped = str(exc)
                break
        await _batch(
            session,
            waiting[start : start + BATCH],
            segment=segment,
            mode=mode,
            client=asked,
            home=home,
            tally=tally,
            moment=moment,
        )
        await session.commit()
    return tally
