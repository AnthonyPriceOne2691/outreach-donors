"""Отбор глазами человека: кто принят, кто отклонён, кем и почему.

**Список строится по ДОМЕНАМ, а не по донорам.** Во включённом судье
отрезанный домен до Ahrefs не доходит и донором не становится — список
доноров его бы просто не показал. А отклонённый без следа — ровно та
слепая зона, ради которой экран заведён: ложно отсеянных иначе не видит
никто.

**Вкладок три, и домен всегда ровно в одной.** Отказ сильнее разбора,
разбор сильнее приёма:

- отклонён — не прошёл пороги ИЛИ отрезан судьёй (или человеком);
- принят — прошёл пороги, и сайт не отрезан;
- к разбору — всё остальное: судья сказал «посмотри», у Ahrefs нет
  данных, метрик ещё нет.

**Решение человека — тип сайта, а не «годен / не годен».** Так
расхождение с машиной считается по каждому слою судьи отдельно, и видно,
какой ошибается: правило, которому верят без взгляда, или модель.
Вердикт машины решением человека НЕ переписывается — расхождение и есть
измеритель.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel

logger = logging.getLogger(__name__)


class UnknownDomainError(ValueError):
    """Домена с таким номером в отборе нет."""


class HumanIntent(StrEnum):
    """Что человек сказал о сайте."""

    PUBLISHER = "publisher"  # площадка: публикует, размещение купить можно
    SELLS_OWN = "sells_own"  # продаёт своё — магазин, производитель, сервис
    NON_COMMERCIAL = "non_commercial"  # размещений не продаёт по уставу


#: Решение человека → совет, сравнимый с советом судьи.
HUMAN_ADVICE: dict[HumanIntent, str] = {
    HumanIntent.PUBLISHER: "accept",
    HumanIntent.SELLS_OWN: "reject",
    HumanIntent.NON_COMMERCIAL: "reject",
}


def human_advice(intent: str) -> str:
    """Совет по решению человека. Незнакомое значение — «посмотри»:
    угадывать за человека нельзя ни в одну сторону."""
    try:
        return HUMAN_ADVICE[HumanIntent(intent)]
    except ValueError:
        logger.warning("решение человека не из списка: %r — считаю «посмотри»", intent)
        return "review"


class Tab(StrEnum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    REJECTED = "rejected"


def _human_advice() -> ColumnElement[Any]:
    return case(
        *(
            (DomainModel.human_intent == intent.value, advice)
            for intent, advice in HUMAN_ADVICE.items()
        ),
        else_=None,
    )


#: Ответ донора → совет. Цена или «продаём» — площадка, «не продаём» — нет.
ANSWER_ADVICE: dict[str, str] = {"sells": "accept", "free": "accept", "declines": "reject"}


def _answer_advice() -> ColumnElement[Any]:
    return case(
        *(
            (DomainModel.seller_answer == answer, advice)
            for answer, advice in ANSWER_ADVICE.items()
        ),
        else_=None,
    )


def site_advice() -> ColumnElement[Any]:
    """Действующий совет о сайте. Ответ самого донора сильнее всех: для
    гест-постинга он и есть правда. Человек сильнее модели."""
    return func.coalesce(_answer_advice(), _human_advice(), DomainModel.judge_recommendation)


def _tab() -> ColumnElement[Any]:
    advice = site_advice()
    return case(
        (
            or_(DonorModel.status == DonorStatus.UNSUITABLE, advice == "reject"),
            Tab.REJECTED.value,
        ),
        (
            (DonorModel.status == DonorStatus.SUITABLE) & or_(advice.is_(None), advice == "accept"),
            Tab.ACCEPTED.value,
        ),
        else_=Tab.REVIEW.value,
    )


def _disagrees() -> ColumnElement[Any]:
    """Человек и машина сказали разное. «Посмотри» машины — не мнение,
    а просьба, и расхождением не считается."""
    return (
        DomainModel.human_intent.is_not(None)
        & DomainModel.judge_recommendation.in_(("accept", "reject"))
        & (_human_advice() != DomainModel.judge_recommendation)
    )


@dataclass(frozen=True, slots=True)
class SelectionFilters:
    tab: Tab = Tab.ACCEPTED
    search: str | None = None
    decided_by: str | None = None
    only_disagreements: bool = False
    only_unreviewed: bool = False
    only_unjudged: bool = False
    only_answered: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SelectionRow:
    domain: DomainModel
    donor: DonorModel | None
    tab: Tab
    disagrees: bool


@dataclass(frozen=True, slots=True)
class LayerScore:
    """Как слой судьи сходится с человеком. Считается только там, где
    человек смотрел: остальное — не точность, а надежда."""

    checked: int = 0
    agreed: int = 0


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    tabs: dict[str, int]
    reviewed: int
    disagreements: int
    layers: dict[str, LayerScore]
    #: Сколько доноров ответили на письмо, продают ли они размещение.
    answered: int = 0
    #: Сходимость слоёв судьи с ответами доноров — точность отбора
    #: в главном для гест-постинга, а не в «издание или продавец».
    answer_layers: dict[str, LayerScore] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SelectionPage:
    rows: list[SelectionRow]
    total: int


def _base() -> Select[Any]:
    """Домены, о которых отбор что-то решил: есть донор или вердикт судьи."""
    return (
        select(DomainModel, DonorModel, _tab().label("tab"), _disagrees().label("disagrees"))
        .outerjoin(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(or_(DonorModel.id.is_not(None), DomainModel.judged_at.is_not(None)))
    )


def _narrow(statement: Select[Any], filters: SelectionFilters) -> Select[Any]:
    statement = statement.where(_tab() == filters.tab.value)
    if filters.search:
        needle = f"%{filters.search.strip().lower()}%"
        statement = statement.where(
            or_(
                DomainModel.host.ilike(needle),
                DonorModel.reject_reason.ilike(needle),
                DomainModel.judge_reason.ilike(needle),
            )
        )
    if filters.decided_by:
        statement = statement.where(DomainModel.judge_decided_by == filters.decided_by)
    if filters.only_disagreements:
        statement = statement.where(_disagrees())
    if filters.only_unreviewed:
        statement = statement.where(DomainModel.human_intent.is_(None))
    if filters.only_answered:
        statement = statement.where(DomainModel.seller_answer.is_not(None))
    if filters.only_unjudged:
        # База, собранная до судьи: вердикта у неё нет, и «принят» здесь
        # значит только «прошёл пороги».
        statement = statement.where(DomainModel.judged_at.is_(None))
    return statement


class SelectionBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, filters: SelectionFilters) -> SelectionPage:
        rows = await self._session.execute(
            _narrow(_base(), filters)
            .order_by(DonorModel.dr.desc().nullslast(), DomainModel.host)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        total = await self._session.execute(
            select(func.count()).select_from(_narrow(_base(), filters).subquery())
        )
        return SelectionPage(
            rows=[
                SelectionRow(domain=domain, donor=donor, tab=Tab(tab), disagrees=bool(disagrees))
                for domain, donor, tab, disagrees in rows.all()
            ],
            total=total.scalar_one(),
        )

    async def summary(self) -> SelectionSummary:
        """Сводка по всему отбору, а не по странице: она отвечает на вопрос
        «насколько можно верить машине», а не «что видно сейчас»."""
        base = _base().subquery()
        tabs = await self._session.execute(select(base.c.tab, func.count()).group_by(base.c.tab))
        reviewed = await self._session.execute(
            select(
                DomainModel.judge_decided_by,
                DomainModel.judge_recommendation,
                _human_advice(),
            ).where(DomainModel.human_intent.is_not(None))
        )
        checked: Counter[str] = Counter()
        agreed: Counter[str] = Counter()
        disagreements = total_reviewed = 0
        for layer, machine, human in reviewed.all():
            total_reviewed += 1
            if machine not in ("accept", "reject"):
                continue
            key = layer or "model"
            checked[key] += 1
            if machine == human:
                agreed[key] += 1
            else:
                disagreements += 1
        answered, answer_layers = await self._answer_scores()
        return SelectionSummary(
            tabs={tab.value: 0 for tab in Tab} | dict(tabs.tuples().all()),
            reviewed=total_reviewed,
            disagreements=disagreements,
            layers={key: LayerScore(checked[key], agreed[key]) for key in checked},
            answered=answered,
            answer_layers=answer_layers,
        )

    async def _answer_scores(self) -> tuple[int, dict[str, LayerScore]]:
        """Судья против ответа донора. «Посмотри» судьи в счёт не идёт."""
        rows = await self._session.execute(
            select(
                DomainModel.judge_decided_by, DomainModel.judge_recommendation, _answer_advice()
            ).where(DomainModel.seller_answer.is_not(None))
        )
        checked: Counter[str] = Counter()
        agreed: Counter[str] = Counter()
        answered = 0
        for layer, machine, answer in rows.all():
            answered += 1
            if machine not in ("accept", "reject"):
                continue
            key = layer or "model"
            checked[key] += 1
            agreed[key] += int(machine == answer)
        return answered, {key: LayerScore(checked[key], agreed[key]) for key in checked}

    async def row(self, domain_id: int) -> SelectionRow:
        found = (await self._session.execute(_base().where(DomainModel.id == domain_id))).first()
        if found is None:
            raise UnknownDomainError(f"Домена №{domain_id} в отборе нет")
        domain, donor, tab, disagrees = found
        return SelectionRow(domain=domain, donor=donor, tab=Tab(tab), disagrees=bool(disagrees))

    async def decide(
        self, domain_id: int, *, intent: HumanIntent | None, note: str | None
    ) -> SelectionRow:
        """Решение человека. `None` — снять своё решение, если ошибся.

        ⚠ Поля модели не трогаются ни при каких условиях: расхождение
        между ними и решением человека — единственный измеритель того,
        как часто модель ошибается.
        """
        current = await self.row(domain_id)
        domain = current.domain
        cleaned = (note or "").strip()[:512] or None
        domain.human_intent = intent.value if intent else None
        domain.human_verdict_at = datetime.now(UTC) if intent else None
        domain.human_note = cleaned if intent else None
        await self._session.flush()
        return await self.row(domain_id)


@dataclass(frozen=True, slots=True)
class ReviewTally:
    """Проверка человеком по набору доменов: сколько смотрел и сколько раз
    разошёлся с судьёй. «Посмотри» судьи расхождением не считается."""

    reviewed: int = 0
    disagreements: int = 0


async def tally_reviews(
    session: AsyncSession, groups: dict[int, list[str]]
) -> dict[int, ReviewTally]:
    """Сводка по группам доменов одним запросом — например, по прогонам.

    Считается при чтении, а не в момент прогона: человек решает на экране
    отбора уже после, и в отчёте, записанном в конце прогона, здесь всегда
    стоял бы ноль.
    """
    hosts = {host for group in groups.values() for host in group}
    if not hosts:
        return {key: ReviewTally() for key in groups}
    rows = await session.execute(
        select(DomainModel.host, _disagrees())
        .where(DomainModel.host.in_(hosts))
        .where(DomainModel.human_intent.is_not(None))
    )
    seen = {host: bool(disagrees) for host, disagrees in rows.all()}
    return {
        key: ReviewTally(
            reviewed=sum(1 for host in group if host in seen),
            disagreements=sum(1 for host in group if seen.get(host)),
        )
        for key, group in groups.items()
    }
