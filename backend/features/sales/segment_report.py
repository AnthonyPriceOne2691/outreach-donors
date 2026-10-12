"""Отчёт прогона гипотезы и решение человека по «на проверку».

**Числа полос читаются из кандидатов, а не из итога задачи**: решение человека, продолжение
прогона и очистка лидов меняют их после задачи, и отчёт показывает, что есть сейчас:
сколько сайтов нашла выдача, принято, на проверку, отсечено — с причинами словами, — без
адреса и сколько лидов заведено (и что с ними сделала очистка). Расход и итог этапов —
из строки прогона (`report`).

**Человек решает только «на проверку»**: принять — сайт идёт к поиску адреса продолжением
прогона; отказать — отсечён. Решение ложится на кандидата с именем и временем, а вердикт
судьи остаётся рядом — по их расхождению меряется судья.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import LeadStatus, SalesHypothesisModel, SalesLeadModel
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)

Candidate = SalesSegmentCandidateModel
#: Строк «на проверку» в ответе: решают по одной, сверху; остальные — после.
REVIEW_ROWS = 100
#: Причин отсева в отчёте — частые первыми.
TOP_REASONS = 12

#: Исход поиска адреса → почему у принятого нет лида, словами.
NO_ADDRESS_WORDS = {
    ContactStatus.NOT_FOUND: "адреса на сайте нет",
    ContactStatus.FORM_ONLY: "только форма на сайте",
    ContactStatus.NO_ANSWER: "сайт не ответил — повтор по сроку",
    ContactStatus.BLOCKED: "сайт закрылся от нас",
    ContactStatus.NO_QUOTA: "квота платного поиска — повтор следующим проходом",
    ContactStatus.RATE_LIMITED: "платный поиск просил подождать — повтор следующим проходом",
    ContactStatus.ERROR: "сбой поиска — повтор следующим проходом",
}


class DecisionRefusedError(ValueError):
    """Решение не ложится: сайт не ждёт человека. Текст — почему."""


class UnknownCandidateError(LookupError):
    """Кандидата нет."""


@dataclass(slots=True)
class RunView:
    """Прогон глазами экрана."""

    run: SalesSegmentRunModel
    hypothesis: str
    lanes: dict[str, int] = field(default_factory=dict)
    #: Причина отсева словами → сколько; частые первыми.
    rejected: list[tuple[str, int]] = field(default_factory=list)
    #: Принятые без лида после поиска: почему словами → сколько.
    no_address: list[tuple[str, int]] = field(default_factory=list)
    #: Принятые, которым поиск адреса ещё предстоит.
    waiting_address: int = 0
    #: Лидов из прогона и что с ними сделала очистка: состояние → сколько.
    leads: dict[str, int] = field(default_factory=dict)
    #: Отсеянные очисткой лиды: код причины → сколько.
    lead_reasons: dict[str, int] = field(default_factory=dict)
    #: Модель не ответила — сейчас, по кандидатам.
    unanswered: int = 0
    review: list[tuple[Candidate, str]] = field(default_factory=list)


async def _lanes(session: AsyncSession, run_id: int) -> dict[str, int]:
    rows = await session.execute(
        select(Candidate.lane, func.count())
        .where(Candidate.run_id == run_id)
        .group_by(Candidate.lane)
    )
    found = {lane.value: 0 for lane in Lane}
    found.update(dict(rows.tuples().all()))
    return found


async def _rejected(session: AsyncSession, run_id: int) -> list[tuple[str, int]]:
    rows = await session.execute(
        select(Candidate.reason, func.count())
        .where(Candidate.run_id == run_id, Candidate.lane == Lane.REJECT)
        .group_by(Candidate.reason)
        .order_by(func.count().desc(), Candidate.reason)
        .limit(TOP_REASONS)
    )
    return [(reason or "причина не записана", count) for reason, count in rows.tuples().all()]


async def _addresses(session: AsyncSession, run_id: int) -> tuple[list[tuple[str, int]], int]:
    """Принятые без лида: почему нет адреса — и сколько ещё ждут поиска."""
    rows = await session.execute(
        select(Candidate.contact_status, func.count())
        .where(
            Candidate.run_id == run_id, Candidate.lane == Lane.ACCEPT, Candidate.lead_id.is_(None)
        )
        .group_by(Candidate.contact_status)
    )
    words: Counter[str] = Counter()
    waiting = 0
    for status, count in rows.tuples().all():
        if status is None:
            waiting += count
        elif status is ContactStatus.FOUND:
            # Адрес найден, а лида нет: он на домене мёртвый или ждёт заведения лидов.
            words["адрес на домене не живой или лид ещё не заведён"] += count
        else:
            words[NO_ADDRESS_WORDS.get(status, str(status))] += count
    return words.most_common(), waiting


async def _leads(session: AsyncSession, run_id: int) -> tuple[dict[str, int], dict[str, int]]:
    rows = await session.execute(
        select(SalesLeadModel.status, SalesLeadModel.rejection_reason, func.count())
        .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(Candidate.run_id == run_id)
        .group_by(SalesLeadModel.status, SalesLeadModel.rejection_reason)
    )
    states: Counter[str] = Counter({status.value: 0 for status in LeadStatus})
    reasons: Counter[str] = Counter()
    for status, reason, count in rows.tuples().all():
        states[status.value] += count
        if status is LeadStatus.REJECTED and reason:
            reasons[reason] += count
    return dict(states), dict(reasons)


async def _review(session: AsyncSession, run_id: int) -> list[tuple[Candidate, str]]:
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.run_id == run_id, Candidate.lane == Lane.REVIEW)
        .order_by(Candidate.id)
        .limit(REVIEW_ROWS)
    )
    return list(rows.tuples().all())


async def run_view(session: AsyncSession, run_id: int) -> RunView | None:
    """Отчёт прогона; `None` — прогона нет."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        return None
    hypothesis = await session.get(SalesHypothesisModel, run.hypothesis_id)
    view = RunView(run, hypothesis.name if hypothesis is not None else f"№{run.hypothesis_id}")
    view.lanes = await _lanes(session, run_id)
    view.rejected = await _rejected(session, run_id)
    view.no_address, view.waiting_address = await _addresses(session, run_id)
    view.leads, view.lead_reasons = await _leads(session, run_id)
    view.unanswered = int(
        await session.scalar(
            select(func.count()).where(
                Candidate.run_id == run_id,
                Candidate.unanswered.is_(True),
                Candidate.lane == Lane.REVIEW,
            )
        )
        or 0
    )
    view.review = await _review(session, run_id)
    return view


async def runs_of(
    session: AsyncSession, hypothesis_id: int, *, limit: int = 20
) -> list[SalesSegmentRunModel]:
    """Прогоны гипотезы — новые первыми."""
    rows = await session.scalars(
        select(SalesSegmentRunModel)
        .where(SalesSegmentRunModel.hypothesis_id == hypothesis_id)
        .order_by(SalesSegmentRunModel.id.desc())
        .limit(limit)
    )
    return list(rows.all())


async def decide(
    session: AsyncSession,
    candidate_id: int,
    *,
    accept: bool,
    author: str,
    now: datetime | None = None,
) -> Candidate:
    """Решение человека по «на проверку». Коммит — за вызывающим."""
    candidate = await session.get(Candidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise UnknownCandidateError(f"сайта сбора №{candidate_id} нет — обновите отчёт прогона")
    if candidate.lane != Lane.REVIEW:
        raise DecisionRefusedError(
            f"сайт не ждёт решения: он уже «{LANE_WORDS.get(candidate.lane, candidate.lane)}» — "
            "обновите отчёт прогона"
        )
    candidate.lane = Lane.ACCEPT if accept else Lane.REJECT
    candidate.reason = "принял человек" if accept else "отказал человек"
    candidate.decided_by = Decider.HUMAN
    candidate.decided_at = now or datetime.now(UTC)
    candidate.decided_by_user = author
    return candidate


#: Полоса словами — для отказов и экрана.
LANE_WORDS: dict[str, str] = {
    Lane.PENDING: "ещё не судили",
    Lane.ACCEPT: "принят",
    Lane.REVIEW: "на проверке",
    Lane.REJECT: "отсечён",
}


def summary(view: RunView) -> dict[str, Any]:
    """Главные числа одной строкой отчёта — для журнала и итога задачи."""
    lanes = view.lanes
    return {
        "hosts": sum(lanes.values()),
        "accepted": lanes.get(Lane.ACCEPT, 0),
        "review": lanes.get(Lane.REVIEW, 0),
        "rejected": lanes.get(Lane.REJECT, 0),
        "no_address": sum(count for _, count in view.no_address),
        "leads": sum(view.leads.values()),
    }
