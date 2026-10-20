"""Что в прогоне ждёт повтора — словами, сроком и можно ли «Повторить сейчас».

Разбор прогона сбора 20.10: у «ждут очистки» и «без адреса» в отчёте прогона не было ни
причины, ни кнопки — лид, за которого Hunter «ещё проверял», и сайт, который не ответил, висели
молча. Теперь отчёт (`segment_report.run_view`) несёт строки ожидания (`Wait`):

- **ждут адреса** — сайты, не ответившие поиску, чей повтор пойдёт сам (`segment_again.py`):
  сколько и когда первый (`segment_queue.retry_at`); «Повторить сейчас» — поиск им сейчас;
- **ждут очистки** — новые лиды прогона, по причине: Hunter «ещё проверяет» (срок —
  `verify_again.py`), первый `unknown` ждёт дня перепроверки (повтор сейчас — платная проверка
  раньше срока, кнопки нет), прочее — словами очистки лида, как они записаны (`cleaning_note`;
  не очищали — «ещё не очищали»). «Повторить сейчас» — очистка ждущих тем же условием, что у
  прохода очистки (`waiting_leads`, `verifier.due`).

Срок — моментом (UTC), а не словами: время по часам человека пишет экран.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.models import LeadStatus, SalesLeadModel
from backend.features.sales.segment_models import SalesSegmentCandidateModel
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.unconfirmed import recheck_on
from backend.features.sales.verifier import due, waits
from backend.features.sales.verify_again import CHECKING

Candidate = SalesSegmentCandidateModel

#: Что ждёт: адрес сайта или очистка лида.
ADDRESS = "address"
CLEANING = "cleaning"
#: Сайт не ответил поиску адреса — повтор пойдёт сам.
SILENT = "сайт не ответил поиску адреса"
#: Лид прогона, которого очистка ещё не видела.
NOT_CLEANED = "ещё не очищали"
#: Причин ожидания очистки словами лида — частые первыми; остальные — одной строкой.
TOP_NOTES = 4
OTHER_NOTES = "другие причины — словами у каждого лида на вкладке лидов"


@dataclass(frozen=True, slots=True)
class Wait:
    """Строка ожидания: что ждёт, почему словами, сколько, когда повтор сам (`at`, UTC; пусто —
    срока нет) и можно ли повторить сейчас (`again`)."""

    kind: str
    words: str
    count: int
    at: datetime | None = None
    again: bool = True


async def waiting_leads(
    session: AsyncSession, run_id: int, *, real: bool, now: datetime
) -> list[SalesLeadModel]:
    """Лиды прогона, которых возьмёт очистка сейчас, — тем же условием, что у прохода очистки
    (`verifier.due`; `real` — проверяльщик живой)."""
    rows = await session.scalars(
        select(SalesLeadModel)
        .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(Candidate.run_id == run_id, due(real, now))
        .order_by(SalesLeadModel.id)
    )
    return list(rows.all())


async def _address(session: AsyncSession, run_id: int, now: datetime) -> list[Wait]:
    """Сайты, не ответившие поиску, чей повтор ещё впереди: сколько и когда первый."""
    ahead = [at for at in await SegmentContactQueue(session, run_id).silent_retries() if at > now]
    return [Wait(ADDRESS, SILENT, len(ahead), min(ahead))] if ahead else []


def _notes(found: Counter[str]) -> list[Wait]:
    """Прочие причины словами лида — частые первыми; хвост — одной строкой."""
    ranked = found.most_common()
    head = [Wait(CLEANING, words, count) for words, count in ranked[:TOP_NOTES]]
    rest = sum(count for _, count in ranked[TOP_NOTES:])
    return head + ([Wait(CLEANING, OTHER_NOTES, rest)] if rest else [])


async def _cleaning(session: AsyncSession, run_id: int, *, real: bool, now: datetime) -> list[Wait]:
    """Новые лиды прогона по причине ожидания."""
    rows = await session.execute(
        select(
            SalesLeadModel.verify_again_at,
            SalesLeadModel.verified_at,
            waits(real, now),
            SalesLeadModel.cleaning_note,
        )
        .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(Candidate.run_id == run_id, SalesLeadModel.status == LeadStatus.NEW)
    )
    checking: list[datetime] = []
    unknown: list[datetime] = []
    notes: Counter[str] = Counter()
    for again_at, verified_at, unconfirmed, note in rows.tuples().all():
        if again_at is not None:
            checking.append(again_at)
        elif unconfirmed and verified_at is not None:
            unknown.append(verified_at)
        else:
            notes[(note or NOT_CLEANED)[:300]] += 1
    found: list[Wait] = []
    if checking:
        found.append(Wait(CLEANING, CHECKING, len(checking), min(checking)))
    if unknown:
        day = f"{recheck_on(min(unknown)):%d.%m.%Y}"
        words = f"адрес не подтверждён (unknown) — перепроверка не раньше {day}"
        found.append(Wait(CLEANING, words, len(unknown), again=False))
    return found + _notes(notes)


async def run_waits(session: AsyncSession, run_id: int, *, real: bool, now: datetime) -> list[Wait]:
    """Что в прогоне ждёт повтора: адреса, затем очистка."""
    return [
        *await _address(session, run_id, now),
        *await _cleaning(session, run_id, real=real, now=now),
    ]
