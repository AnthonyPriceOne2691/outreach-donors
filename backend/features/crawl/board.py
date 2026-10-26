"""Обходы на экране: кого можно обойти, чем кончился последний обход, что идёт.

Отбор тот же, что у консоли (`targets.choose`: донор, принятый человеком,
известная и свежая цена, не поставщик) — у кнопки и команды разные правила
разошлись бы молча. Здесь только сборка того, что видит человек: цена донора
рядом с его последним обходом и ход идущих обходов.

**Доля закрытых страниц — у строки обхода** (сверка ТЗ 11.10.2026). Числа
здоровья обход пишет в свою запись с самого начала (`crawl/health.py`), а экран
их не показывал: «обойдён: 1 000 страниц» не отличал донора, отдавшего всё,
от донора, закрывшего от нас треть запросов, — а от этой доли считается смета
Этапа 2 (`okf/crawl-access.md`).

**Почему обход кончился — тоже у строки** (23.10.2026): «обойдён: N страниц»
не говорил, почему не 1 000 — кончилась карта сайта или час на донора при долгой
паузе сайта. Откуда брались адреса, дочитана ли карта, какую паузу просит сайт и
сколько шёл обход — из отчёта законченного обхода (`CrawlReport.as_dict`); у
идущего и записанного до отчёта — пусто.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import targets
from backend.features.crawl.lifecycle import REASON_KEY
from backend.features.runs.reasons import readable

#: Незаконченные обходы — их ход экран обновляет сам.
ACTIVE = (CrawlStatus.QUEUED, CrawlStatus.RUNNING)

#: Откуда обход брал адреса (`CrawlReport.source`): карта сайта, ссылки со страниц или
#: карта, а после неё ссылки. «none» — обход не дошёл до списка адресов (robots.txt,
#: главная не открылась): источника нет.
SOURCES = frozenset({"sitemap", "links", "sitemap+links"})


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
    #: Чего каскаду не хватило: уровень → словами (`fetch.Degradation`) — прокси отказали
    #: подряд все, сервис рендеринга отключился, потолок Этапа 2 выбран, браузер не поднялся.
    degradation: dict[str, str]
    #: Доля закрытых страниц: ответов «закрылся от нас» от всех запросов обхода
    #: (`CrawlHealth.blocked_share`). Пусто — обход не кончился (числа пишет конец
    #: обхода), не сделал ни одного запроса или записан до замера здоровья.
    blocked_share: float | None = None
    #: Сколько запросов за долей: «закрыто 100%» на трёх запросах и на тысяче — разное.
    requests: int | None = None
    #: Доля 403/429/5xx/таймаут в окне последних запросов (`CrawlHealth.failure_share`) —
    #: мера здоровья ТЗ («за последние 500 запросов: > 10% — темп вдвое ниже, > 30% —
    #: пауза»), на конец обхода. `window` — сколько запросов в окне: меньше 500, если
    #: столько не набралось.
    failure_share: float | None = None
    window: int | None = None
    #: Откуда брались адреса — значение из `SOURCES`; пусто — обход не дошёл до списка,
    #: не кончился или записан до отчёта.
    source: str | None = None
    #: Дочитана ли карта сайта: `False` — «в карте больше адресов нет» сказано по
    #: неполной карте (файл не открылся, упёрлись в потолок файлов или адресов карты).
    sitemap_complete: bool | None = None
    #: Пауза между страницами, которую просит robots.txt сайта, с; пусто — не просит.
    #: Как есть: обход ждёт не меньше своей паузы и не больше своего потолка паузы.
    crawl_delay: float | None = None
    #: Сколько обход шёл, с, вместе с продолжениями.
    elapsed_sec: float | None = None
    #: Свой срок этого обхода, с, — если обход его записал (`time_budget_sec` отчёта).
    #: Пусто — срок общий, из настроек.
    time_budget_sec: float | None = None

    @classmethod
    def of(cls, run: CrawlRunModel) -> CrawlLine:
        stats: dict[str, Any] = run.stats or {}
        links = stats.get("links_found")
        share, asked = _closed(stats)
        failures, window = _window(stats, asked)
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
            blocked_share=share,
            requests=asked,
            failure_share=failures,
            window=window,
            degradation={str(k): str(v) for k, v in (run.degradation or {}).items()},
            source=_source(stats.get("source")),
            sitemap_complete=_flag(stats.get("sitemap_complete")),
            crawl_delay=_seconds(stats.get("crawl_delay")),
            elapsed_sec=_seconds(stats.get("elapsed_sec")),
            time_budget_sec=_seconds(stats.get("time_budget_sec")),
        )


def _source(value: object) -> str | None:
    """Источник адресов из отчёта; незнакомое значение и «none» — пусто, а не догадка."""
    return value if isinstance(value, str) and value in SOURCES else None


def _flag(value: object) -> bool | None:
    """Да или нет из отчёта; чего нет или не то — пусто."""
    return value if isinstance(value, bool) else None


def _seconds(value: object) -> float | None:
    """Секунды из отчёта. Флаг (`True` — тоже `int`) и отрицательное — не секунды."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _window(stats: dict[str, Any], requests: int | None) -> tuple[float | None, int | None]:
    """Доля отказов в окне здоровья и сколько запросов в окне: меньше окна, если
    столько не набралось. Запросов нет — доли нет."""
    failures = stats.get("failure_share")
    if requests is None or not isinstance(failures, int | float):
        return None, None
    return float(failures), min(requests, cfg.HEALTH_WINDOW)


def _closed(stats: dict[str, Any]) -> tuple[float | None, int | None]:
    """Доля закрытых страниц и запросов за ней — из чисел здоровья обхода
    (`CrawlHealth.report`). Запросов нет или чисел нет — `(None, None)`: доля
    от нуля запросов не доля."""
    requests, blocked = stats.get("requests"), stats.get("blocked")
    if not isinstance(requests, int) or not isinstance(blocked, int) or requests <= 0:
        return None, None
    return blocked / requests, requests


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
