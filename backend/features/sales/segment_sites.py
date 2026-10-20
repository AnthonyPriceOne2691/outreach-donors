"""Все сайты прогона гипотезы — по одному: полоса, кто решил и почему, цитата, ключ и итог.

Тест прогона: человек видел только «на проверку» (до сотни строк) и сводку отказов по причинам —
ни принятых, ни отсечённых по сайтам. Судья сортирует, решает человек (`okf/run-review.md`): ему
нужен полный список, чтобы сверить судью и правку описания сегмента. Здесь он:

- **фильтр по полосе** — принят, на проверку, судья отказал бы (отказ судьи в наблюдении:
  решать не обязательно), отсечён (правилом, моделью или человеком), ещё не судили, все; и по
  тому, **кто решил**: правило, модель, язык сайта, человек;
- **страницами** — номер и размер называет сервер, всего — числом: без молчаливой обрезки;
  порядок полный, до номера кандидата — строка со стыка страниц не пропадает и не двоится;
- **итог сайта** — тем же правилом, что числа отчёта (`segment_report._addresses`): правило
  одно и живёт здесь (`address_words`) — лид (со ссылкой), только форма, адреса нет, не
  искали, повтор по сроку; компанию держит живой лид продаж — «компания уже в работе гипотезы
  «…»» (`company_hold.held_sites`, одно правило с отчётом прогона).

Пересуд прогона (`segment_rejudge.py`) кладёт в отчёт прогона, какой была полоса сайта до него:
список показывает «было» у сменивших полосу и «стало бы» у сайтов с лидом (их полоса не
меняется), а фильтр «сменили полосу» — только их.

**Решение человека — со словами судьи** (тест продаж 20.10: с чем человек не согласился, видно не
было): у решённого человеком сайта — что сказал бы судья (`segment_undo.judged_before`), и можно ли
решение отменить (`segment_undo.undoable`: лида из сайта нет). Строка после решения и его отмены —
тем же правилом, что строка списка (`site_row`): экран ставит её на место той, по которой решали.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.core.domain import ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.sales import company_hold
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_rejudge_report import Change, changes
from backend.features.sales.segment_undo import judged_before, undoable

Candidate = SalesSegmentCandidateModel

#: Сайтов на странице: строка с причиной и цитатой высокая, полсотни — экран-другой.
PAGE_SIZE = 50

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
#: Адрес найден, а лида нет: он на домене мёртвый или ждёт заведения лидов.
FOUND_NO_LEAD = "адрес на домене не живой или лид ещё не заведён"
NOT_SEARCHED = "адрес ещё не искали — найдёт продолжение прогона"
LEAD_MADE = "лид заведён"


def address_words(status: ContactStatus) -> str:
    """Почему у принятого нет лида после поиска адреса — словами. Одно правило у чисел
    отчёта прогона и у итога сайта в списке."""
    if status is ContactStatus.FOUND:
        return FOUND_NO_LEAD
    return NO_ADDRESS_WORDS.get(status, str(status))


class SiteLane(StrEnum):
    """Вкладка списка: полоса сайта, все — или сменившие полосу при пересуде."""

    ALL = "all"
    ACCEPT = Lane.ACCEPT.value
    REVIEW = Lane.REVIEW.value
    WOULD_REJECT = Lane.WOULD_REJECT.value
    REJECT = Lane.REJECT.value
    PENDING = Lane.PENDING.value
    CHANGED = "changed"


class OutcomeKind(StrEnum):
    """Чем кончился сайт после судьи."""

    LEAD = "lead"
    FORM_ONLY = "form_only"
    NO_ADDRESS = "no_address"
    NOT_SEARCHED = "not_searched"
    #: Повтор по сроку, квота, сбой, мёртвый адрес, компания уже в работе — слова говорят, что именно.
    OTHER = "other"


_KINDS = {
    ContactStatus.FORM_ONLY: OutcomeKind.FORM_ONLY,
    ContactStatus.NOT_FOUND: OutcomeKind.NO_ADDRESS,
}


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: OutcomeKind
    words: str
    lead_id: int | None = None
    #: Лид — и что с ним сделала очистка: состояние и код причины отказа.
    lead_status: str | None = None
    lead_reason: str | None = None


def outcome(
    lane: str,
    status: ContactStatus | None,
    lead: tuple[int, str, str | None] | None = None,
    held: str | None = None,
) -> Outcome | None:
    """Итог сайта: лид — по ссылке кандидата; у принятого без лида — компанию держит живой лид
    продаж (`held`, слова `company_hold`) или исход поиска адреса; у остальных полос итога нет
    (адрес им не ищут)."""
    if lead is not None:
        lead_id, state, reason = lead
        return Outcome(OutcomeKind.LEAD, LEAD_MADE, lead_id, state, reason)
    if lane != Lane.ACCEPT:
        return None
    if held is not None:
        return Outcome(OutcomeKind.OTHER, held)
    if status is None:
        return Outcome(OutcomeKind.NOT_SEARCHED, NOT_SEARCHED)
    return Outcome(_KINDS.get(status, OutcomeKind.OTHER), address_words(status))


@dataclass(frozen=True, slots=True)
class SiteRow:
    """Сайт прогона глазами списка."""

    candidate: Candidate
    host: str
    outcome: Outcome | None
    #: Пересуд сменил полосу (или сменил бы — у сайта с лидом): какой она была и что вышло.
    change: Change | None = None
    #: Решил человек — что сказал судья: его слова тем видом, каким их кладёт судья. Пусто — решил
    #: не человек (слова судьи и так в причине) или вердикта не было (не ответил, выключен).
    judge: str | None = None

    @property
    def undoable(self) -> bool:
        return undoable(self.candidate)


@dataclass(frozen=True, slots=True)
class SitesAsk:
    run_id: int
    lane: SiteLane = SiteLane.ALL
    decider: Decider | None = None
    page: int = 1


@dataclass(slots=True)
class SitesPage:
    rows: list[SiteRow]
    #: Сколько сайтов под фильтром — всего, а не на странице.
    total: int
    page: int
    limit: int
    #: Числа вкладок: полоса → сайтов, `all` — все сайты прогона, `changed` — сменили полосу.
    lanes: dict[str, int] = field(default_factory=dict)
    #: Кто решил — внутри вкладки: правило, модель, человек; `none` — никто (ещё не судили,
    #: модель не ответила, судья выключен).
    deciders: dict[str, int] = field(default_factory=dict)


def _lane_filter(lane: SiteLane, changed: dict[int, Change]) -> ColumnElement[bool] | None:
    if lane is SiteLane.ALL:
        return None
    if lane is SiteLane.CHANGED:
        return Candidate.id.in_(list(changed))
    return Candidate.lane == lane.value


def _decider_filter(decider: Decider | None) -> ColumnElement[bool] | None:
    return None if decider is None else Candidate.decided_by == decider.value


async def _lanes(session: AsyncSession, run_id: int, changed: dict[int, Change]) -> dict[str, int]:
    rows = await session.execute(
        select(Candidate.lane, func.count())
        .where(Candidate.run_id == run_id)
        .group_by(Candidate.lane)
    )
    found = {lane.value: 0 for lane in SiteLane}
    found.update(dict(rows.tuples().all()))
    found[SiteLane.ALL] = sum(found[lane.value] for lane in Lane)
    found[SiteLane.CHANGED] = len(changed)
    return found


async def _deciders(
    session: AsyncSession, run_id: int, lane: ColumnElement[bool] | None
) -> dict[str, int]:
    query = select(Candidate.decided_by, func.count()).where(Candidate.run_id == run_id)
    if lane is not None:
        query = query.where(lane)
    rows = await session.execute(query.group_by(Candidate.decided_by))
    found = {decider.value: 0 for decider in Decider} | {"none": 0}
    for decider, count in rows.tuples().all():
        found[decider or "none"] += count
    return found


async def _leads(session: AsyncSession, ids: list[int]) -> dict[int, tuple[int, str, str | None]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(SalesLeadModel.id, SalesLeadModel.status, SalesLeadModel.rejection_reason).where(
            SalesLeadModel.id.in_(ids)
        )
    )
    return {lead: (lead, str(status), reason) for lead, status, reason in rows.tuples().all()}


async def _outcomes(
    session: AsyncSession, run_id: int, candidates: list[Candidate]
) -> dict[int, Outcome | None]:
    """Итоги сайтов страницы — лиды по ссылке кандидата и удержанные компании (`company_hold`)
    двумя чтениями на страницу."""
    leads = await _leads(session, [c.lead_id for c in candidates if c.lead_id is not None])
    held = await company_hold.held_sites(session, run_id, [c.id for c in candidates])
    return {
        c.id: outcome(c.lane, c.contact_status, leads.get(c.lead_id or 0), held.get(c.id))
        for c in candidates
    }


async def _judges(
    session: AsyncSession, run: SalesSegmentRunModel, candidates: list[Candidate]
) -> dict[int, str]:
    """Слова судьи у решённых человеком сайтов страницы — одним чтением кэша вердиктов. Вердикта
    не было (модель не ответила, судья выключен) — слов нет: колонка «Судья» так и говорит."""
    said = await judged_before(session, candidates, JudgeMode(run.mode), run.language)
    return {
        c.id: found.reason
        for c in candidates
        if (found := said.get(c.id)) is not None
        and found.verdict is not None
        and found.reason is not None
    }


async def sites_page(session: AsyncSession, ask: SitesAsk) -> SitesPage | None:
    """Страница сайтов прогона под фильтром; `None` — прогона нет."""
    # Заново из базы: отчёт пересуда пишется SQL-правкой своего ключа, мимо объекта прогона.
    run = await session.get(SalesSegmentRunModel, ask.run_id, populate_existing=True)
    if run is None:
        return None
    changed = changes(run.report)
    where: list[Any] = [Candidate.run_id == ask.run_id]
    lane = _lane_filter(ask.lane, changed)
    where += [part for part in (lane, _decider_filter(ask.decider)) if part is not None]
    total = await session.scalar(select(func.count()).select_from(Candidate).where(*where))
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(*where)
        .order_by(Candidate.id)
        .offset((ask.page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    found = list(rows.tuples().all())
    candidates = [candidate for candidate, _ in found]
    outcomes = await _outcomes(session, ask.run_id, candidates)
    judges = await _judges(session, run, candidates)
    return SitesPage(
        rows=[
            SiteRow(
                candidate,
                host,
                outcomes[candidate.id],
                changed.get(candidate.id),
                judges.get(candidate.id),
            )
            for candidate, host in found
        ],
        total=int(total or 0),
        page=ask.page,
        limit=PAGE_SIZE,
        lanes=await _lanes(session, ask.run_id, changed),
        deciders=await _deciders(session, ask.run_id, lane),
    )


async def site_row(session: AsyncSession, candidate: Candidate) -> SiteRow:
    """Один сайт — строкой списка, тем же правилом: итог, «было» пересуда, слова судьи у решения
    человека. После решения и его отмены экран ставит её на место той, по которой решали."""
    # Заново из базы: отчёт пересуда пишется SQL-правкой своего ключа, мимо объекта прогона.
    run = await session.get(SalesSegmentRunModel, candidate.run_id, populate_existing=True)
    host = await session.scalar(
        select(DomainModel.host).where(DomainModel.id == candidate.domain_id)
    )
    if run is None or host is None:  # кандидат держит прогон и домен ключами — не бывает
        raise LookupError(f"у сайта сбора №{candidate.id} нет прогона или домена")
    outcomes = await _outcomes(session, run.id, [candidate])
    judges = await _judges(session, run, [candidate])
    change = changes(run.report).get(candidate.id)
    return SiteRow(candidate, host, outcomes[candidate.id], change, judges.get(candidate.id))
