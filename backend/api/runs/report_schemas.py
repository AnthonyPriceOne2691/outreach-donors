"""Отчёт прогона по маршруту — карточки отчёта (`runs/outcome.py`).

Своим модулем, а не в `schemas.py` (ревью 20.10.2026): схемы маршрутов прогона стояли у
потолка длины файла, а отчёт растёт с каждым новым шагом прогона — пороги, причины
словами, исключённые и «не проверен».
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.api.runs.schemas import RunCard
from backend.api.settings.schemas import ArticleCard
from backend.features.runs.outcome import Domains, Named, Rejection, RunOutcome
from backend.features.runs.refusals import Threshold


class RunThresholds(BaseModel):
    """Пороги, с которыми прогон запущен: отсев называется вместе с ними, а пороги
    версионируются — сегодняшние на экране «Пороги» могут быть уже другими."""

    min_dr: int
    min_org_traffic: int
    min_refdomains: int
    min_keywords: int
    #: DR выше — крупный сайт: «кому не пишем».
    max_dr: int
    #: Регион: страна прогона в топ-N стран по трафику ИЛИ доля не меньше порога.
    geo_top_n: int
    geo_min_share: float


class RunDomains(BaseModel):
    """Домены прогона по шагам — `runs.outcome.Domains` с посчитанными итогами."""

    found: int | None
    excluded: int
    fresh: int
    to_check: int
    #: Прошли проверку Ahrefs с любым вердиктом: подходит, не подходит, не проверен.
    checked: int
    suitable: int
    unsuitable: int
    unchecked: int
    #: Новых, до Ahrefs не дошедших: отрезал судья или прогон остановился раньше.
    not_reached: int
    judge_enforced: bool
    resumes: int
    #: Свежих прогон судил сам, по сохранённым метрикам: их вердикты — в числах выше.
    fresh_judged: bool

    @classmethod
    def of(cls, domains: Domains) -> RunDomains:
        return cls(
            found=domains.found,
            excluded=domains.excluded,
            fresh=domains.fresh,
            to_check=domains.to_check,
            checked=domains.checked,
            suitable=domains.suitable,
            unsuitable=domains.unsuitable,
            unchecked=domains.unchecked,
            not_reached=domains.not_reached,
            judge_enforced=domains.judge_enforced,
            resumes=domains.resumes,
            fresh_judged=domains.fresh_judged,
        )


class RejectionCard(BaseModel):
    """Сколько доменов отсеял порог. Порог — кодом, слова и значение — на экране."""

    threshold: Threshold
    count: int
    #: Причина как записана — только у незнакомой (`other`).
    said: str | None = None

    @classmethod
    def of(cls, rejection: Rejection) -> RejectionCard:
        return cls(threshold=rejection.threshold, count=rejection.count, said=rejection.said)


class NamedCard(BaseModel):
    """Сколько доменов не предложено по причине — словами сервера."""

    said: str
    count: int
    #: «Кому не пишем» ТЗ: такой домен не берут по правилу.
    not_writing: bool

    @classmethod
    def of(cls, named: Named) -> NamedCard:
        return cls(said=named.said, count=named.count, not_writing=named.not_writing)


class RunReportView(BaseModel):
    """Отчёт прогона: что проверено, что прошло, по какому порогу отсеяно и во что
    обошлось (ТЗ, «после прогона — отчёт»).

    `domains` пусто — итога по доменам ещё нет: прогон в очереди или идёт первой
    попыткой. Расход при этом уже есть: журнал пишется на каждом чекпоинте.
    """

    run: RunCard
    thresholds: RunThresholds
    domains: RunDomains | None
    rejected: list[RejectionCard]
    #: Расход по журналу — всё, что записано за этим прогоном, статьями.
    spending: list[ArticleCard]
    #: Запросы к Ahrefs, которые обслужил кэш провайдера: не трата, но число.
    free_requests: int
    #: Исключённые гейтом до Ahrefs — по причине; «не проверен» — почему.
    excluded: list[NamedCard]
    unchecked: list[NamedCard]

    @classmethod
    def of(cls, outcome: RunOutcome) -> RunReportView:
        settings = outcome.settings
        return cls(
            run=RunCard.of(outcome.row),
            thresholds=RunThresholds(
                min_dr=settings.min_dr,
                min_org_traffic=settings.min_org_traffic,
                min_refdomains=settings.min_refdomains,
                min_keywords=settings.min_keywords,
                max_dr=settings.max_dr,
                geo_top_n=settings.geo_top_n,
                geo_min_share=settings.geo_min_share,
            ),
            domains=None if outcome.domains is None else RunDomains.of(outcome.domains),
            rejected=[RejectionCard.of(rejection) for rejection in outcome.rejected],
            spending=[ArticleCard.of(article) for article in outcome.spending],
            free_requests=outcome.free_requests,
            excluded=list(map(NamedCard.of, outcome.excluded)),
            unchecked=list(map(NamedCard.of, outcome.unchecked)),
        )
