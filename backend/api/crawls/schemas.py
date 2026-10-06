"""Что уходит и приходит по маршрутам обходов Этапа 2."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.crawl.board import CrawlLine, DonorLine

#: Сколько доноров ставится одним нажатием. Обходчиков четыре, обход — до
#: получаса: полсотни доноров — это уже шесть часов очереди, и больше за раз
#: человек осознанно не выбирает.
MAX_HOSTS = 50


class CrawlRow(BaseModel):
    """Один обход: где он и чем кончился. `max_pages` — потолок, к которому
    идёт ход «340 из 1000»."""

    id: int
    host: str
    status: CrawlStatus
    outcome: CrawlOutcome | None = None
    stop_reason: StopReason | None = None
    pages: int
    max_pages: int
    articles: int
    links: int | None = None
    resumes: int
    requested_by: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    #: Почему обход стоит, продолжен или остановлен — словами человека.
    reason: str | None = None

    @classmethod
    def of(cls, line: CrawlLine, *, max_pages: int) -> CrawlRow:
        return cls(
            id=line.id,
            host=line.host,
            status=line.status,
            outcome=line.outcome,
            stop_reason=line.stop_reason,
            pages=line.pages,
            max_pages=max_pages,
            articles=line.articles,
            links=line.links,
            resumes=line.resumes,
            requested_by=line.requested_by,
            created_at=line.created_at,
            finished_at=line.finished_at,
            reason=line.reason,
        )


class DonorRow(BaseModel):
    """Донор, которого можно обойти: цена — основание, обход — что о нём знаем."""

    host: str
    price: float | None = None
    priced_at: datetime | None = None
    crawl: CrawlRow | None = None

    @classmethod
    def of(cls, line: DonorLine, *, max_pages: int) -> DonorRow:
        return cls(
            host=line.host,
            price=float(line.price) if line.price is not None else None,
            priced_at=line.priced_at,
            crawl=CrawlRow.of(line.crawl, max_pages=max_pages) if line.crawl else None,
        )


class TargetsView(BaseModel):
    """Кого можно обойти и кого нельзя — числами и словами, что делать."""

    donors: list[DonorRow]
    no_price: int
    stale_price: int
    supplier: int
    notes: list[str] = Field(default_factory=list)
    max_pages: int
    #: Сколько обходчиков слушает очередь. `None` — не спросили; 0 — поставленное
    #: никто не возьмёт.
    workers: int | None = None


class CrawlsView(BaseModel):
    rows: list[CrawlRow]
    active: int
    workers: int | None = None


class LaunchBody(BaseModel):
    """Кого обойти: домены из списка доноров со свежей ценой."""

    hosts: list[str] = Field(min_length=1)

    @field_validator("hosts")
    @classmethod
    def _not_too_many(cls, hosts: list[str]) -> list[str]:
        if len(hosts) > MAX_HOSTS:
            raise PydanticCustomError(
                "too_many_hosts",
                f"За раз ставим не больше {MAX_HOSTS} доноров, выбрано {len(hosts)}: "
                "обходчиков четыре, обход — до получаса",
            )
        return hosts


class LaunchResult(BaseModel):
    """Что поставлено, что уже шло, что не принято и почему."""

    queued: dict[str, int]
    busy: list[str]
    failed: list[str]
    refused: dict[str, str]
    workers: int | None = None
