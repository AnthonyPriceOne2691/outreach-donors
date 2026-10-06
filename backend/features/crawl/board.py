"""Обходы на экране: кого можно обойти, чем кончился последний обход, что идёт.

Отбор тот же, что у консоли (`targets.choose`: известная и свежая цена, не
поставщик) — у кнопки и команды разные правила разошлись бы молча. Здесь
только сборка того, что видит человек: цена донора рядом с его последним
обходом и ход идущих обходов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
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
    """Донор, которого можно обойти: цена — основание, обход — что о нём знаем."""

    host: str
    price: Decimal | None
    priced_at: datetime | None
    crawl: CrawlLine | None


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
        host: (price, priced_at)
        for host, price, priced_at in (
            await session.execute(
                select(DomainModel.host, DonorModel.last_price, DonorModel.last_price_at)
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DomainModel.host.in_(chosen.hosts))
            )
        ).all()
    }
    latest = await latest_by_host(session, [host.lower() for host in chosen.hosts])
    donors = [
        DonorLine(
            host=host,
            price=prices.get(host, (None, None))[0],
            priced_at=prices.get(host, (None, None))[1],
            crawl=latest.get(host.lower().removeprefix("www.")),
        )
        for host in chosen.hosts
    ]
    return Board(donors=donors, chosen=chosen)


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
