"""Схемы резерва: домены прогона, которые не прошли, и «Взять на рассмотрение»."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.features.review.queue import Decision
from backend.features.review.reserve import TAKE_LIMIT, ReservePage, ReserveRow, TakeReport
from backend.features.review.reserve_rules import CountryFit, Group


class FitView(BaseModel):
    """Страна прогона в топ-5 домена: место и доля; верхняя страна и её доля."""

    place: int | None
    share: float | None
    top: str | None
    top_share: float | None
    #: Страны спрашивали: без них места нет, потому что не знаем, а не потому что нет.
    asked: bool
    #: В разбивке одна верхняя страна — остальные не запрашивались.
    partial: bool

    @classmethod
    def of(cls, fit: CountryFit) -> FitView:
        return cls(
            place=fit.place,
            share=fit.share,
            top=fit.top,
            top_share=fit.top_share,
            asked=fit.asked,
            partial=fit.partial,
        )


class ReserveCard(BaseModel):
    """Строка резерва: прогон, домен, почему не прошёл, метрики и можно ли взять."""

    run_id: int
    #: Страна прогона: вердикт вынесен для неё.
    country: str
    domain_id: int
    #: Донор домена — карточка, куда ведёт имя; пусто — гейт снял домен до Ahrefs.
    donor_id: int | None
    host: str
    group: Group
    #: Порог отказа кодом (`runs.refusals.Threshold`) или причина гейта.
    cause: str | None
    #: Почему не прошёл — словами вердикта, страна — названием.
    reason: str | None
    dr: int | None
    org_traffic: int | None
    refdomains: int | None
    keywords: int | None
    fit: FitView
    #: Уже в очереди этого прогона: предложен, принят или отклонён.
    queued: Decision | None
    takeable: bool
    #: Почему взять нельзя — словами; пусто — можно.
    refusal: str | None

    @classmethod
    def of(cls, row: ReserveRow) -> ReserveCard:
        donor = row.donor
        raw = (donor.metrics if donor is not None else None) or {}
        return cls(
            run_id=row.run_id,
            country=row.country,
            domain_id=row.domain_id,
            donor_id=None if donor is None else donor.id,
            host=row.host,
            group=row.group,
            cause=row.cause,
            reason=row.said,
            dr=None if donor is None else donor.dr,
            org_traffic=None if donor is None else donor.org_traffic,
            refdomains=_number(raw.get("refdomains")),
            keywords=_number(raw.get("org_keywords")),
            fit=FitView.of(row.fit),
            queued=None if row.queued is None else Decision(row.queued),
            takeable=row.takeable,
            refusal=row.refusal,
        )


def _number(value: object) -> int | None:
    """Число из записанного ответа Ahrefs: флаг и строка — не число."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


class ReserveRunView(BaseModel):
    """Прогон фильтра: заголовок резерва прогона."""

    id: int
    country: str
    keywords: int
    #: Домены конкурентов у прогона по их ссылкам; `None` — по ключам (`RunModel.competitor_targets`).
    competitors: list[str] | None = None
    created_at: datetime


class ReserveView(BaseModel):
    rows: list[ReserveCard]
    total: int
    #: Строк на странице — число сервера: экран считает страницы им.
    limit: int
    #: Группа причины → сколько доменов под остальными фильтрами.
    groups: dict[str, int]
    #: Страна прогонов → сколько доменов в их резерве.
    countries: dict[str, int]
    #: Страна в топ-5 доменов резерва → у скольких.
    geos: dict[str, int]
    #: Прогон фильтра; пусто — резерв всех прогонов.
    run: ReserveRunView | None

    @classmethod
    def of(cls, page: ReservePage, *, limit: int) -> ReserveView:
        run = page.run
        return cls(
            rows=list(map(ReserveCard.of, page.rows)),
            total=page.total,
            limit=limit,
            groups=page.groups,
            countries=page.countries,
            geos=page.geos,
            run=None
            if run is None
            else ReserveRunView(
                id=run.id,
                country=run.country,
                keywords=len(run.keywords or []),
                competitors=run.competitor_targets,
                created_at=run.created_at,
            ),
        )


class TakeItem(BaseModel):
    run_id: int
    domain_id: int


class TakeBody(BaseModel):
    """Какие домены и из резерва каких прогонов взять на рассмотрение."""

    items: list[TakeItem] = Field(min_length=1, max_length=TAKE_LIMIT)


class TakeResult(BaseModel):
    """Что вышло: взяты (по прогонам), уже были в очереди, отказано — почему."""

    taken: int
    #: Номер прогона → взятые в его очередь домены.
    runs: dict[int, list[str]]
    already: list[str]
    refused: dict[str, str]

    @classmethod
    def of(cls, report: TakeReport) -> TakeResult:
        return cls(
            taken=report.count,
            runs=report.taken,
            already=report.already,
            refused=report.refused,
        )
