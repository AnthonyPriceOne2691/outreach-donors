"""Что уходит и приходит по маршрутам вкладки «Обход доноров» (29.10.2026, `crawl/donor_page.py`).

Строка донора — та же, что у панели (`DonorRow`): значок обхода, причина и место в очереди
рисуются одним кодом на обеих. Своё у вкладки — страница, счётчики и «Обойти всех по фильтру».
"""

from __future__ import annotations

from typing import Literal

from fastapi import Query
from pydantic import BaseModel, Field

from backend.api.crawls.schemas import CrawlCeilings, DonorRow
from backend.config import crawl as crawl_cfg
from backend.features.crawl import donor_page
from backend.features.crawl.board import QueueState
from backend.features.crawl.donor_page import DonorQuery, Facets, Price, Sort, Status

#: Откуда цена — значения `PriceSource`; файлом заведённые встанут сюда же своим словом.
Source = Literal["reply", "manual"]


class DonorFilter(BaseModel):
    """Условия вкладки — без страницы: их же берёт «Обойти всех по фильтру»."""

    status: Status | None = None
    price: Price | None = None
    source: Source | None = None
    search: str = Field(default="", max_length=253)
    sort: Sort = Sort.PRICED

    def query(self, *, page: int = 1, size: int = donor_page.PAGE_SIZE) -> DonorQuery:
        return DonorQuery(
            status=self.status,
            price=self.price,
            source=self.source,
            search=self.search,
            sort=self.sort,
            page=page,
            size=size,
        )


def donor_params(
    status: Status | None = Query(default=None, description="статус последнего обхода"),
    price: Price | None = Query(default=None, description="цена: в сроке, старше срока, нет"),
    source: Source | None = Query(default=None, description="откуда цена"),
    search: str = Query(default="", max_length=253, description="подстрока домена"),
    sort: Sort = Query(default=Sort.PRICED, description="порядок строк"),
    page: int = Query(default=1, ge=1, le=100_000),
    size: int = Query(
        default=donor_page.PAGE_SIZE,
        ge=1,
        le=max(donor_page.SIZES),
        description="доноров на странице",
    ),
) -> DonorQuery:
    """Параметры адреса → вопрос вкладки. Незнакомое значение — отказ схемы: адрес собирает
    экран из своих же значений (`crawlFilters.ts`)."""
    return DonorFilter(status=status, price=price, source=source, search=search, sort=sort).query(
        page=page, size=size
    )


class CountsView(BaseModel):
    """Счётчики: по статусу последнего обхода и по цене."""

    status: dict[Status, int]
    price: dict[Price, int]

    @classmethod
    def of(cls, facets: Facets) -> CountsView:
        return cls(status=dict(facets.status), price=dict(facets.price))


class DonorsView(CrawlCeilings):
    """Страница вкладки: строки, сколько всего, счётчики и чем обходить. Потолки обхода и мера
    здоровья — для «i» вкладки, как у панели (`TargetsView`): числами сервера, а не копией."""

    rows: list[DonorRow]
    total: int
    page: int
    limit: int
    counts: CountsView
    #: Сколько под условиями можно поставить в обход сейчас — число у «Обойти всех по фильтру».
    startable: int
    #: Сколько «Обойти всех по фильтру» ставит за раз: больше — «10 000 из 12 345» на кнопке.
    bulk_max: int = donor_page.BULK_MAX
    #: Сколько обходчиков слушает очередь; `None` — не спросили.
    workers: int | None = None
    #: Сколько обычно идёт обход донора, с, — для «≈ N ч при K обходчиках».
    typical_crawl_sec: float
    max_pages: int
    health_window: int = Field(default_factory=lambda: crawl_cfg.HEALTH_WINDOW)
    slow_down_share: float = Field(default_factory=lambda: crawl_cfg.SLOW_DOWN_SHARE)
    stop_share: float = Field(default_factory=lambda: crawl_cfg.STOP_SHARE)
    max_seconds_slow: float = Field(default_factory=lambda: crawl_cfg.MAX_SECONDS_SLOW)

    @classmethod
    def of(
        cls,
        found: donor_page.DonorPage,
        query: DonorQuery,
        *,
        queue: QueueState,
        workers: int | None,
        typical: float,
        max_pages: int,
    ) -> DonorsView:
        return cls(
            rows=[
                DonorRow.of(line, max_pages=max_pages, queue=queue, workers=workers)
                for line in found.rows
            ],
            total=found.total,
            page=query.page,
            limit=query.size,
            counts=CountsView.of(found.facets),
            startable=found.startable,
            workers=workers,
            typical_crawl_sec=typical,
            max_pages=max_pages,
        )


class SummaryView(BaseModel):
    """Обход доноров одной строкой — для «Проверки»: счётчики без условий и что идёт."""

    counts: CountsView
    startable: int
    #: Обходов в очереди и на ходу.
    active: int
    workers: int | None = None


class BulkBody(DonorFilter):
    """«Обойти всех по фильтру»: те же условия, что у страницы, и сколько человек видел.

    `expected` — число с кнопки, которое человек подтвердил: под фильтр могло попасть больше,
    пока он думал, — встанет не больше, чем он видел."""

    expected: int = Field(ge=1, le=donor_page.BULK_MAX)


class BulkResult(BaseModel):
    """Что вышло: сколько доноров попало под фильтр и поставлено ли сразу.

    Больше `routes.MAX_HOSTS` — ставится фоновой задачей (`workers/crawl_bulk.py`): строка
    обхода на донора — своей транзакцией, и тысяча доноров в одном запросе держала бы экран
    минуту. Тогда `queued` пусто, а ход виден по счётчику «в очереди»."""

    asked: int
    deferred: bool
    queued: int | None = None
    busy: int | None = None
    failed: int | None = None
    #: Номер фоновой задачи, если ставит она.
    job_id: str | None = None
    workers: int | None = None
