"""Панель рекламодателей в ответах API: выборка, шаги, качество загрузок, режим рассылки.

Числа считают `crawl/panel.py` и `crawl/load_quality.py`, экран своего счёта не ведёт.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from backend.api.crawls.schemas import CrawlRow
from backend.api.letters.autosend_schemas import QueueAutosendView
from backend.config import crawl as crawl_cfg
from backend.features.core.models.crawl import DonorLoadModel
from backend.features.crawl import load_quality
from backend.features.crawl.panel import DonorSteps, Funnel, Range


class PanelLoad(BaseModel):
    """Партия в списке выбора и в шапке выборки."""

    id: int
    at: datetime
    kind: str
    file_name: str | None
    hosts: int

    @classmethod
    def of(cls, load: DonorLoadModel, hosts: int) -> PanelLoad:
        return cls(
            id=load.id,
            at=load.created_at,
            kind=load.kind,
            file_name=load.file_name,
            hosts=hosts,
        )


class PanelDonors(BaseModel):
    """Доноры выборки: сколько поставлено и где они сейчас."""

    added: int
    started: int
    running: int
    waiting: int
    refused: int
    idle: int

    @classmethod
    def of(cls, steps: DonorSteps) -> PanelDonors:
        return cls(
            added=steps.added,
            started=steps.started,
            running=steps.running,
            waiting=steps.waiting,
            refused=steps.refused,
            idle=steps.idle,
        )


class PanelAdvertisers(BaseModel):
    """Рекламодатели выборки: шаги вложены, каждый следующий — из предыдущего."""

    found: int
    fresh: int
    repeat: int
    promoted: int
    with_address: int
    sent: int
    delivered: int

    @classmethod
    def of(cls, found: Funnel) -> PanelAdvertisers:
        return cls(
            found=found.tally.found,
            fresh=found.fresh,
            repeat=found.repeat,
            promoted=found.tally.promoted,
            with_address=found.tally.with_address,
            sent=found.tally.sent,
            delivered=found.delivered,
        )


class PanelView(BaseModel):
    """Окно панели: что выбрано, партии на выбор и шаги выборки."""

    range: Range
    #: С какой минуты считается выборка; у «всего времени» пусто.
    since: datetime | None
    #: Выбранная партия; вне выборки «партия» пусто.
    load: PanelLoad | None
    #: Последние партии — для выбора. Загрузки до учёта партий здесь не бывает.
    loads: list[PanelLoad]
    donors: PanelDonors
    advertisers: PanelAdvertisers


class QualityRowView(BaseModel):
    """Строка качества загрузок; пустые `selling_links` и `share` — у обходов этих чисел ещё нет."""

    at: datetime
    kind: str
    load_id: int | None
    day: date | None
    file_name: str | None
    donors: int
    host: str | None
    scanned: int
    pages: int
    selling_links: int | None
    selling_pages: int
    share: float | None

    @classmethod
    def of(cls, row: load_quality.QualityRow) -> QualityRowView:
        return cls(
            at=row.at,
            kind=row.kind,
            load_id=row.load_id,
            day=row.day,
            file_name=row.file_name,
            donors=row.donors,
            host=row.host,
            scanned=row.scanned,
            pages=row.pages,
            selling_links=row.selling_links,
            selling_pages=row.selling_pages,
            share=row.share,
        )


class QualityView(BaseModel):
    rows: list[QualityRowView]
    total: int
    page: int
    limit: int


class QualityHostRow(BaseModel):
    """Домен строки качества: обход тем же видом, что на вкладке обхода."""

    host: str
    crawl: CrawlRow | None
    selling_links: int

    @classmethod
    def of(cls, row: load_quality.HostRow) -> QualityHostRow:
        crawl = row.crawl
        return cls(
            host=row.host,
            crawl=(
                None
                if crawl is None
                else CrawlRow.of(crawl, max_pages=crawl_cfg.MAX_PAGES_PER_DONOR)
            ),
            selling_links=row.selling_links,
        )


class QualityHostsView(BaseModel):
    rows: list[QualityHostRow]
    total: int
    page: int
    limit: int


class PanelMailingView(BaseModel):
    """Режим очереди рекламодателей: у ссылок и у бизнесов ниши очереди разные."""

    links: QueueAutosendView
    niche: QueueAutosendView
