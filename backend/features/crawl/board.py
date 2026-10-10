"""Обходы на экране: кого можно обойти, чем кончился последний обход, что идёт.

Отбор тот же, что у консоли (`targets.choose`: донор, принятый человеком,
известная и свежая цена, не поставщик) — у кнопки и команды разные правила
разошлись бы молча. Здесь только сборка того, что видит человек: цена донора
рядом с его последним обходом и ход идущих обходов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import targets
from backend.features.crawl.lifecycle import REASON_KEY
from backend.features.runs.reasons import readable

#: Незаконченные обходы — их ход экран обновляет сам.
ACTIVE = (CrawlStatus.QUEUED, CrawlStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class CrawlLine:
    """Один обход, как его видит человек."""

    id: int
    host: str
    status: CrawlStatus
    outcome: CrawlOutcome | None
    stop_reason: StopReason | None
    pages: int
    articles: int
    links: int | None
    resumes: int
    requested_by: str | None
    created_at: datetime
    finished_at: datetime | None
    reason: str | None

    @classmethod
    def of(cls, run: CrawlRunModel) -> CrawlLine:
        stats: dict[str, Any] = run.stats or {}
        links = stats.get("links_found")
        return cls(
            id=run.id,
            host=run.host,
            status=run.status,
            outcome=run.outcome,
            stop_reason=run.stop_reason,
            pages=run.pages_opened,
            articles=run.articles,
            links=links if isinstance(links, int) else None,
            resumes=run.resumes,
            requested_by=run.requested_by,
            created_at=run.created_at,
            finished_at=run.finished_at,
            reason=readable(stats.get(REASON_KEY)),
        )


@dataclass(frozen=True, slots=True)
class DonorLine:
    """Донор, которого можно обойти: цена — основание, обход — что о нём знаем.

    Валюта — рядом с ценой: конвертации нет, и «150» без неё на экране читалось
    долларами, даже названное в евро. Источник — из ответа или вручную
    (`donors/manual_price.py`): человек видит, на чём держится «мы дешевле».
    """

    host: str
    price: Decimal | None
    priced_at: datetime | None
    crawl: CrawlLine | None
    currency: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Board:
    donors: list[DonorLine]
    chosen: targets.Targets


async def eligible(session: AsyncSession, *, limit: int) -> Board:
    """Доноры со свежей ценой (не больше `limit`) и последний обход каждого."""
    chosen = await targets.choose(session, limit=limit)
    if not chosen.hosts:
        return Board(donors=[], chosen=chosen)
    prices = {
        row.host: row
        for row in (
            await session.execute(
                select(
                    DomainModel.host,
                    DonorModel.last_price,
                    DonorModel.last_price_at,
                    DonorModel.last_price_currency,
                    DonorModel.last_price_source,
                )
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DomainModel.host.in_(chosen.hosts))
            )
        ).all()
    }
    latest = await latest_by_host(session, [host.lower() for host in chosen.hosts])
    donors = [
        _line(host, prices.get(host), latest.get(host.lower().removeprefix("www.")))
        for host in chosen.hosts
    ]
    return Board(donors=donors, chosen=chosen)


def _line(host: str, priced: Row[Any] | None, crawl: CrawlLine | None) -> DonorLine:
    """Строка донора: цена с валютой и источником — из его строки в базе."""
    if priced is None:
        return DonorLine(host=host, price=None, priced_at=None, crawl=crawl)
    return DonorLine(
        host=host,
        price=priced.last_price,
        priced_at=priced.last_price_at,
        crawl=crawl,
        currency=priced.last_price_currency,
        source=priced.last_price_source,
    )


async def latest_by_host(session: AsyncSession, hosts: list[str]) -> dict[str, CrawlLine]:
    """Последний обход каждого донора, любой: идущий, законченный, остановленный."""
    names = sorted({host.lower().removeprefix("www.") for host in hosts})
    if not names:
        return {}
    newest = (
        select(func.max(CrawlRunModel.id))
        .where(CrawlRunModel.host.in_(names))
        .group_by(CrawlRunModel.host)
    )
    rows = (
        (await session.execute(select(CrawlRunModel).where(CrawlRunModel.id.in_(newest))))
        .scalars()
        .all()
    )
    return {row.host: CrawlLine.of(row) for row in rows}


async def recent(session: AsyncSession, *, limit: int) -> list[CrawlLine]:
    """Последние обходы, новые сверху."""
    rows = (
        (
            await session.execute(
                select(CrawlRunModel).order_by(CrawlRunModel.id.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [CrawlLine.of(row) for row in rows]


async def active_count(session: AsyncSession) -> int:
    query = select(func.count()).select_from(CrawlRunModel).where(CrawlRunModel.status.in_(ACTIVE))
    return int((await session.execute(query)).scalar_one())
