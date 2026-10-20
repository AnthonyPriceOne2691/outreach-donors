"""Кэш вердиктов судьи сегмента: что судья уже знает о домене под тем же описанием.

Ключ строки — домен, гипотеза, версия промпта и отпечаток описания (`sales_segment_verdicts`): правка
описания — новый вопрос модели. С v4 модель отдаёт факты, а полосу ставит код по фактам и полю
гипотезы (`segment_rules`), поэтому кэш отдаёт **факты**, а не полосу: правка поля гипотезы или
таблицы правил не делает факты старыми, и пересуд ставит полосу заново без модели. Строка правила
(«кому не пишем» по имени, пустой текст) отдаётся вердиктом как есть.

Строка без фактов и не правило (модель не дала цитаты, сочинила, ответ не разобран) — не вердикт, а
несработавшая проверка: кэш её не отдаёт, модель спросят снова. Свежесть — `VERDICT_TTL_DAYS`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_judge import PROMPT_VERSION, SegmentVerdict
from backend.features.sales.segment_models import Decider, Lane, SalesSegmentVerdictModel

#: Срок вердикта: чем сайт живёт, он меняет раз в годы — как у судьи площадки.
VERDICT_TTL_DAYS = 180

Verdict = SalesSegmentVerdictModel


@dataclass(frozen=True, slots=True)
class Known:
    """Что кэш знает о домене: факты модели — полосу по ним ставит код, — или вердикт правила."""

    facts: SiteFacts | None = None
    ruled: SegmentVerdict | None = None
    model: str | None = None


def _known(row: Verdict) -> Known | None:
    if row.decided_by == Decider.RULE:
        return Known(ruled=SegmentVerdict(Lane(row.verdict), row.reason, decided_by=Decider.RULE))
    facts = SiteFacts.from_json(row.facts) if row.facts is not None else None
    return Known(facts=facts, model=row.model) if facts is not None else None


async def cached(
    session: AsyncSession,
    *,
    hypothesis_id: int,
    digest: str,
    domain_ids: Sequence[int],
    now: datetime,
) -> dict[int, Known]:
    """Свежее знание той же версии промпта и того же описания сегмента — по домену."""
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
    found = {row.domain_id: _known(row) for row in rows.all()}
    return {domain_id: known for domain_id, known in found.items() if known is not None}


def verdict_row(
    *,
    domain_id: int,
    hypothesis_id: int,
    verdict: SegmentVerdict,
    digest: str,
    moment: datetime,
) -> dict[str, Any]:
    """Строка кэша: вердикт, его слова и факты, если они есть."""
    return {
        "domain_id": domain_id,
        "hypothesis_id": hypothesis_id,
        "prompt_version": PROMPT_VERSION,
        "segment_digest": digest,
        "verdict": verdict.lane.value,
        "quote": verdict.quote,
        "reason": verdict.reason[:1000],
        "model": verdict.model,
        "decided_by": verdict.decided_by.value,
        "tokens": verdict.tokens,
        "facts": verdict.facts.as_json() if verdict.facts is not None else None,
        "updated_at": moment,
    }


async def remember(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Вердикты чекпоинта — в кэш: новый ключ — строка, тот же — поверх."""
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
                "facts": fresh.facts,
                "updated_at": fresh.updated_at,
            },
        )
    )


async def relabel(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Полоса по фактам из кэша (пересуд, правка поля гипотезы) — поверх полосы и слов строки.
    Факты, их цена и срок — те, что заплачены: вызова модели у такой полосы не было."""
    for row in rows:
        await session.execute(
            update(Verdict)
            .where(
                Verdict.domain_id == row["domain_id"],
                Verdict.hypothesis_id == row["hypothesis_id"],
                Verdict.prompt_version == row["prompt_version"],
                Verdict.segment_digest == row["segment_digest"],
            )
            .values(
                verdict=row["verdict"],
                quote=row["quote"],
                reason=row["reason"],
                decided_by=row["decided_by"],
            )
            .execution_options(synchronize_session=False)
        )
