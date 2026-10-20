"""Отчёт прогона гипотезы и решение человека по «на проверку».

**Числа полос читаются из кандидатов, а не из итога задачи**: решение человека, продолжение
прогона и очистка лидов меняют их после задачи, и отчёт показывает, что есть сейчас:
сколько сайтов нашла выдача, принято, на проверку, отсечено — с причинами словами, — без
адреса и сколько лидов заведено (и что с ними сделала очистка). Расход и итог этапов —
из строки прогона (`report`).

**Человек решает только «на проверку»** (полосы ожидания человека — `AWAITS_HUMAN`): принять —
сайт идёт к поиску адреса продолжением прогона; отказать — отсечён. Решение ложится на кандидата
с именем и временем, а вердикт судьи остаётся рядом — по их расхождению меряется судья. То же
решение второй раз (двойной клик, повтор запроса) — не отказ и не вторая запись журнала: сайт уже
там, куда его просят (`Decision.repeated`); противоположное по решённому — по-прежнему отказ
словами. Вернуть решение — отменой (`segment_undo.py`), пока из сайта не заведён лид.

**Сайты по ключу** — сколько новых для гипотезы сайтов принёс каждый ключ и сколько из них
принято (`KeySites`): по ним видно, что почти повторы общего ключа почти ничего не добавили.

**Ход прогона** — запись прохода и живое «сделано» судьи и адресов из кандидатов
(`segment_progress.live`); плитки сходятся и во время судьи: «ещё не судили» — своим числом.

**«Ждут адреса» — тем же условием, что у прохода** (разбор прогона сбора 20.10: счёт брал лишь
не искавшиеся, и сайт с наступившим сроком повтора кнопки не получал): поиск адреса — не
искавшиеся и с наступившим сроком (`segment_queue`), лид из адреса, что уже есть на домене, —
условием этапа лидов (`segment_leads.lead_ready`). «Без адреса — почему» — по исходу поиска,
как и было: сайт с наступившим сроком в нём со своими словами и в счёте кнопки. Что ждёт повтора,
когда и можно ли «Повторить сейчас», — строками ожидания (`segment_waits.py`).

**Списка сайтов в отчёте нет** — только числа: отчёт опрашивается раз в три секунды, пока прогон
идёт, а сайты по одному (и «на проверку» тоже) отдаёт список сайтов прогона страницами
(`segment_sites.py`), и экран спрашивает его, только когда список открыт.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales import company_hold, segment_progress
from backend.features.sales.models import LeadStatus, SalesHypothesisModel, SalesLeadModel
from backend.features.sales.segment_leads import lead_ready
from backend.features.sales.segment_models import (
    AWAITS_HUMAN,
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.segment_sites import address_words
from backend.features.sales.segment_waits import Wait, run_waits
from backend.features.sales.verifier import live

Candidate = SalesSegmentCandidateModel
#: Причин отсева в отчёте — частые первыми.
TOP_REASONS = 12


class DecisionRefusedError(ValueError):
    """Решение не ложится: сайт не ждёт человека. Текст — почему."""


class UnknownCandidateError(LookupError):
    """Кандидата нет."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Решение человека: кандидат после него и было ли оно повтором уже записанного."""

    candidate: SalesSegmentCandidateModel
    #: Сайт уже решён человеком так же — запись не менялась, журналу писать нечего.
    repeated: bool = False


@dataclass(frozen=True, slots=True)
class KeySites:
    """Сайты прогона по ключу: новые для гипотезы и сколько из них принято. Сайт, который нашли
    несколько ключей, засчитан первому из них по списку (`Candidate.keyword`)."""

    sites: int
    accepted: int


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
    #: Принятые, которых возьмёт проход: поиск адреса сейчас и лид из готового адреса.
    waiting_address: int = 0
    #: Что ждёт повтора — адреса и очистка: словами, сроком и можно ли повторить сейчас.
    waits: list[Wait] = field(default_factory=list)
    #: Лидов из прогона и что с ними сделала очистка: состояние → сколько.
    leads: dict[str, int] = field(default_factory=dict)
    #: Отсеянные очисткой лиды: код причины → сколько.
    lead_reasons: dict[str, int] = field(default_factory=dict)
    #: Модель не ответила — сейчас, по кандидатам.
    unanswered: int = 0
    #: Ход прохода (`segment_progress.live`); пусто — записи хода нет.
    progress: dict[str, Any] | None = None
    #: Сайты по ключу — сколько принёс каждый (`KeySites`); ключа без сайтов здесь нет.
    keyword_sites: dict[str, KeySites] = field(default_factory=dict)


async def _keyword_sites(session: AsyncSession, run_id: int) -> dict[str, KeySites]:
    accepted = func.count().filter(Candidate.lane == Lane.ACCEPT)
    rows = await session.execute(
        select(Candidate.keyword, func.count(), accepted)
        .where(Candidate.run_id == run_id)
        .group_by(Candidate.keyword)
    )
    return {
        keyword: KeySites(sites, taken)
        for keyword, sites, taken in rows.tuples().all()
        if keyword is not None
    }


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
    """Принятые без лида: почему нет адреса после поиска — и сколько возьмёт проход тем же
    условием, что у него: поиск сейчас — не искавшиеся и с наступившим сроком повтора
    (`segment_queue`), и лид из адреса, что уже есть на домене (`segment_leads.lead_ready`).
    Сайт, чью компанию держит живой лид продаж, адрес не ищет и поиска не ждёт: итог — словами
    `company_hold`."""
    rows = await session.execute(
        select(Candidate.contact_status, func.count())
        .where(
            Candidate.run_id == run_id,
            Candidate.lane == Lane.ACCEPT,
            Candidate.lead_id.is_(None),
            Candidate.contact_status.is_not(None),
            ~company_hold.held(Candidate.domain_id),
        )
        .group_by(Candidate.contact_status)
    )
    words = await company_hold.held_in_run(session, run_id)
    for status, count in rows.tuples().all():
        if status is not None:  # исход есть у каждого, кого поиск видел (условие выборки)
            # Слова — одним правилом с итогом сайта в списке сайтов прогона.
            words[address_words(status)] += count
    searched = await SegmentContactQueue(session, run_id).pending_count()
    ready = await session.scalar(select(func.count(Candidate.id)).where(lead_ready(run_id)))
    return words.most_common(), searched + int(ready or 0)


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
    view.waits = await run_waits(session, run_id, real=live(), now=datetime.now(UTC))
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
    view.progress = await segment_progress.live(session, run)
    view.keyword_sites = await _keyword_sites(session, run_id)
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


def _settled(candidate: Candidate, lane: Lane) -> Decision:
    """Сайт уже не ждёт человека: тот же ответ человека — повтор, иначе — отказ словами."""
    if candidate.lane == lane and candidate.decided_by == Decider.HUMAN:
        return Decision(candidate, repeated=True)
    raise DecisionRefusedError(
        f"сайт не ждёт решения: он уже «{LANE_WORDS.get(candidate.lane, candidate.lane)}» — "
        "обновите отчёт прогона"
    )


async def decide(
    session: AsyncSession,
    candidate_id: int,
    *,
    accept: bool,
    author: str,
    now: datetime | None = None,
) -> Decision:
    """Решение человека по «на проверку». Коммит — за вызывающим.

    Тот же ответ по сайту, уже решённому человеком так же, — повтор (`repeated`): двойной
    клик по «Принять» отвечал 409, хотя сайт и так принят. Противоположный — отказ словами:
    решение человека второе нажатие не переворачивает."""
    candidate = await session.get(Candidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise UnknownCandidateError(f"сайта сбора №{candidate_id} нет — обновите отчёт прогона")
    lane = Lane.ACCEPT if accept else Lane.REJECT
    if candidate.lane not in AWAITS_HUMAN:
        return _settled(candidate, lane)
    candidate.lane = lane
    candidate.reason = "принял человек" if accept else "отказал человек"
    candidate.decided_by = Decider.HUMAN
    candidate.decided_at = now or datetime.now(UTC)
    candidate.decided_by_user = author
    return Decision(candidate)


#: Полоса словами — для отказов и экрана.
LANE_WORDS: dict[str, str] = {
    Lane.PENDING: "ещё не судили",
    Lane.ACCEPT: "принят",
    Lane.REVIEW: "на проверке",
    Lane.REJECT: "отсечён",
}


def summary(view: RunView) -> dict[str, Any]:
    """Главные числа одной строкой отчёта — для журнала и итога задачи. Сайтов — сумма
    полос: принято, на проверку, отсечено и «ещё не судили» (`pending`) — без последней
    плитки во время судьи не сходились."""
    lanes = view.lanes
    return {
        "hosts": sum(lanes.values()),
        "accepted": lanes.get(Lane.ACCEPT, 0),
        "review": lanes.get(Lane.REVIEW, 0),
        "rejected": lanes.get(Lane.REJECT, 0),
        "pending": lanes.get(Lane.PENDING, 0),
        "no_address": sum(count for _, count in view.no_address),
        "leads": sum(view.leads.values()),
    }
