"""Вкладка «Обход доноров»: доноры страницами, фильтры и счётчики — на сервере (29.10.2026).

Слово Anthony 29.10.2026: «доноров будут тысячи, а панель вываливает простыню — листать два
года; нужно компактно, но с фильтрами по статусам». Панель «Проверки» грузила до 500 доноров
целиком (`board.eligible`) и считала всё на экране. Здесь — один запрос страницы с порядком,
счётчики по статусу и по цене — той же выборкой без своего условия (нажать счётчик — тот же
фильтр), и «Обойти всех по фильтру» — теми же условиями (`startable_hosts`).

**Кто в выборке.** Те же доноры, что у отбора обхода (`targets.choose`): принятые человеком,
годные по порогам, не «кому не пишем» и не поставщики. Но не только со свежей ценой: с
протухшей и без цены — тоже, фильтром «Цена», чтобы на вкладке было видно, кого не обойти и
почему. Обходят по-прежнему только со свежей ценой: это правило запуска (`routes._why_not`) и
«Обойти всех по фильтру» (`startable`).

**Статус — по последнему обходу донора**: не обходили, в очереди, идёт, обойдён (исход «ok»),
частично, не пустил (robots.txt запрещает или не отдан, защита от ботов, главная не открылась —
`forbidden`, `blocked`, `failed`), остановлен разбором зависших. Последний обход — самый новый
номер обхода у хоста; хост обхода — без «www.» и строчными (`launch.launch`), и донор с ним
сводится тем же правилом.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from backend.config import filters as cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, DonorStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl.board import CrawlLine, DonorLine
from backend.features.crawl.suppliers import supplier_hosts
from backend.features.donors.standing import is_donor
from backend.features.runs.exclusions import not_writing


class Status(StrEnum):
    """Статус последнего обхода донора — фильтр и счётчик вкладки."""

    NEVER = "never"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    REFUSED = "refused"
    STOPPED = "stopped"


class Price(StrEnum):
    """Есть ли у донора цена и в сроке ли она (`filters.PRICE_TTL_DAYS`)."""

    FRESH = "fresh"
    STALE = "stale"
    NONE = "none"


class Sort(StrEnum):
    """Порядок строк. Умолчание — как у отбора обхода: свежая цена сверху."""

    PRICED = "priced"
    CRAWLED = "crawled"
    HOST = "host"
    PRICE = "price"
    LINKS = "links"


#: Размеры страницы на выбор у экрана, умолчание — середина; сервер держит и любой до самого большого.
SIZES = (25, 50, 100)
PAGE_SIZE = 50
#: Больше за одно «Обойти всех по фильтру» не ставится: очередь из десяти тысяч — это
#: дни работы обходчиков, и больше человек осознанно не выбирает.
BULK_MAX = 10_000
#: Сколько идёт обход донора, если замера ещё нет: тест-группа 29.10.2026 — 18–29 минут на
#: 1 000 страниц, один донор — полтора часа.
TYPICAL_FALLBACK_SEC = 1_500.0
#: По скольким последним законченным обходам меряется обычная длина обхода и сколько страниц
#: нужно, чтобы обход шёл в счёт: отказ сайта с первой страницы длину обхода не говорит.
TYPICAL_SAMPLE = 50
TYPICAL_MIN_PAGES = 50

#: Исходы, при которых сайт не пустил с самого начала.
REFUSED_OUTCOMES = (CrawlOutcome.FORBIDDEN, CrawlOutcome.BLOCKED, CrawlOutcome.FAILED)
#: Незаконченные обходы: второй такой у донора сервер не заводит.
ACTIVE = (CrawlStatus.QUEUED, CrawlStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class DonorQuery:
    """Что спросила вкладка: условия, порядок, страница."""

    status: Status | None = None
    price: Price | None = None
    #: Откуда цена: `reply` — из ответа донора, `manual` — вписал человек (`PriceSource`).
    source: str | None = None
    #: Подстрока домена; пусто — не сужать.
    search: str = ""
    sort: Sort = Sort.PRICED
    page: int = 1
    size: int = PAGE_SIZE


@dataclass(frozen=True, slots=True)
class Facets:
    """Счётчики вкладки: по статусу — без условия на статус, по цене — без условия на цену."""

    status: dict[Status, int] = field(default_factory=dict)
    price: dict[Price, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DonorPage:
    rows: list[DonorLine]
    #: Сколько доноров под условиями всего, а не на этой странице.
    total: int
    facets: Facets
    #: Сколько из них можно поставить в обход сейчас: свежая цена и обход не идёт и не ждёт.
    startable: int


_RUN = aliased(CrawlRunModel, name="last_run")


def _crawl_key() -> ColumnElement[str]:
    """Хост донора так, как его пишет обход: строчными и без «www.»."""
    return func.regexp_replace(func.lower(DomainModel.host), r"^www\.", "")


def status_of() -> ColumnElement[str]:
    """Статус последнего обхода словом `Status`."""
    return case(
        (_RUN.id.is_(None), Status.NEVER.value),
        (_RUN.status == CrawlStatus.QUEUED, Status.QUEUED.value),
        (_RUN.status == CrawlStatus.RUNNING, Status.RUNNING.value),
        (_RUN.status == CrawlStatus.STOPPED, Status.STOPPED.value),
        (_RUN.outcome == CrawlOutcome.OK, Status.DONE.value),
        (_RUN.outcome == CrawlOutcome.PARTIAL, Status.PARTIAL.value),
        else_=Status.REFUSED.value,
    )


def price_of(border: datetime) -> ColumnElement[str]:
    """Цена словом `Price`: нет, старше срока, в сроке."""
    return case(
        (
            or_(DonorModel.last_price.is_(None), DonorModel.last_price_at.is_(None)),
            Price.NONE.value,
        ),
        (DonorModel.last_price_at < border, Price.STALE.value),
        else_=Price.FRESH.value,
    )


def _border(now: datetime) -> datetime:
    return now - timedelta(days=cfg.PRICE_TTL_DAYS)


def _latest() -> Any:
    """Номер последнего обхода донора — подзапросом на строку донора (`LATERAL`), по индексу
    хоста обходов. Соединение со сводкой «хост → последний номер» база вела вложенным циклом:
    хост донора сводится выражением (`_crawl_key`), и на 10 000 донорах счётчик статусов шёл
    9 с — 42 млн сравнений (замер 29.10.2026); подзапрос на строку — десять тысяч поисков по
    индексу."""
    return (
        select(CrawlRunModel.id.label("run_id"))
        .where(CrawlRunModel.host == _crawl_key())
        .order_by(CrawlRunModel.id.desc())
        .limit(1)
        .lateral("latest")
    )


def _base(columns: list[Any], suppliers: set[str]) -> Select[Any]:
    """Доноры отбора обхода с последним обходом каждого — без условий вкладки."""
    latest = _latest()
    statement = (
        select(*columns)
        .select_from(DomainModel)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .outerjoin(latest, true())
        .outerjoin(_RUN, _RUN.id == latest.c.run_id)
        .where(is_donor(), DonorModel.status == DonorStatus.SUITABLE, ~not_writing())
    )
    if suppliers:
        statement = statement.where(func.lower(DomainModel.host).not_in(sorted(suppliers)))
    return statement


def _search(text: str) -> ColumnElement[bool]:
    """Подстрока домена; знаки шаблона LIKE — буквально."""
    escaped = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(DomainModel.host).like(f"%{escaped}%", escape="\\")


def _where(
    statement: Select[Any], query: DonorQuery, border: datetime, *, counted: bool = False
) -> Select[Any]:
    """Условия вкладки; `counted` — без статуса и срока цены: их сводит таблица счётчиков."""
    if query.status is not None and not counted:
        statement = statement.where(status_of() == query.status.value)
    if query.price is not None and not counted:
        statement = statement.where(price_of(border) == query.price.value)
    if query.source is not None:
        statement = statement.where(DonorModel.last_price_source == query.source)
    if query.search.strip():
        statement = statement.where(_search(query.search.strip()))
    return statement


def _can_start(border: datetime) -> ColumnElement[bool]:
    """Можно ли поставить донора в обход сейчас: цена в сроке, обход не идёт и не ждёт."""
    idle = or_(_RUN.id.is_(None), _RUN.status.not_in(ACTIVE))
    return and_(price_of(border) == Price.FRESH.value, idle)


def _order(sort: Sort) -> list[Any]:
    """Порядок строк; домен — вторым ключом, чтобы страницы не перемешивались."""
    host = DomainModel.host.asc()
    if sort is Sort.CRAWLED:
        return [_RUN.created_at.asc().nullsfirst(), host]
    if sort is Sort.HOST:
        return [host]
    if sort is Sort.PRICE:
        return [DonorModel.last_price.desc().nullslast(), host]
    if sort is Sort.LINKS:
        return [_RUN.stats["links_found"].as_integer().desc().nullslast(), host]
    return [DonorModel.last_price_at.desc().nullslast(), host]


@dataclass(frozen=True, slots=True)
class _Cell:
    """Сколько доноров в клетке «статус × срок цены × можно ли обойти»."""

    status: str
    price: str
    startable: bool
    count: int


async def _cells(
    session: AsyncSession, query: DonorQuery, *, suppliers: set[str], border: datetime
) -> list[_Cell]:
    """Все числа вкладки одним запросом: доноры под условиями, кроме статуса и срока цены, —
    по клеткам «статус × срок × можно обойти» (их не больше 42). Счётчики, «всего» и «можно
    обойти» складываются из клеток (`_tally`): пять проходов по выборке стоили бы впятеро."""
    inner = (
        _where(_base([DomainModel.id], suppliers), query, border, counted=True)
        .add_columns(
            status_of().label("status"),
            price_of(border).label("price"),
            _can_start(border).label("startable"),
        )
        .subquery()
    )
    grouped = select(inner.c.status, inner.c.price, inner.c.startable, func.count()).group_by(
        inner.c.status, inner.c.price, inner.c.startable
    )
    return [
        _Cell(str(status), str(price), bool(startable), int(count))
        for status, price, startable, count in (await session.execute(grouped)).all()
    ]


def _status_ok(cell: _Cell, query: DonorQuery) -> bool:
    return query.status is None or cell.status == query.status.value


def _price_ok(cell: _Cell, query: DonorQuery) -> bool:
    return query.price is None or cell.price == query.price.value


def _tally(cells: list[_Cell], query: DonorQuery) -> tuple[Facets, int, int]:
    """Счётчики (каждый — без своего условия), «всего» и «можно обойти» — из клеток."""
    facets = Facets(
        status={
            status: sum(c.count for c in cells if c.status == status.value and _price_ok(c, query))
            for status in Status
        },
        price={
            price: sum(c.count for c in cells if c.price == price.value and _status_ok(c, query))
            for price in Price
        },
    )
    chosen = [cell for cell in cells if _status_ok(cell, query) and _price_ok(cell, query)]
    total = sum(cell.count for cell in chosen)
    return facets, total, sum(cell.count for cell in chosen if cell.startable)


def _line(row: Any) -> DonorLine:
    run: CrawlRunModel | None = row.last_run
    return DonorLine(
        host=row.host,
        price=row.last_price,
        priced_at=row.last_price_at,
        crawl=CrawlLine.of(run) if run is not None else None,
        currency=row.last_price_currency,
        source=row.last_price_source,
        price_state=row.price_state,
    )


async def page(
    session: AsyncSession, query: DonorQuery, *, now: datetime | None = None
) -> DonorPage:
    """Страница доноров вкладки со счётчиками и числом тех, кого можно обойти."""
    border = _border(now or datetime.now(UTC))
    suppliers = await supplier_hosts(session, now=now)
    columns = [
        DomainModel.host,
        DonorModel.last_price,
        DonorModel.last_price_at,
        DonorModel.last_price_currency,
        DonorModel.last_price_source,
        price_of(border).label("price_state"),
        _RUN,
    ]
    listed = _where(_base(columns, suppliers), query, border)
    rows = (
        await session.execute(
            listed.order_by(*_order(query.sort))
            .limit(query.size)
            .offset((query.page - 1) * query.size)
        )
    ).all()
    facets, total, startable = _tally(
        await _cells(session, query, suppliers=suppliers, border=border), query
    )
    return DonorPage(
        rows=[_line(row) for row in rows], total=total, facets=facets, startable=startable
    )


async def summary(session: AsyncSession, *, now: datetime | None = None) -> tuple[Facets, int]:
    """Счётчики всей выборки без условий и сколько можно обойти — для «Проверки»."""
    border = _border(now or datetime.now(UTC))
    suppliers = await supplier_hosts(session, now=now)
    every = DonorQuery()
    facets, _, startable = _tally(
        await _cells(session, every, suppliers=suppliers, border=border), every
    )
    return facets, startable


async def startable_hosts(
    session: AsyncSession, query: DonorQuery, *, limit: int = BULK_MAX, now: datetime | None = None
) -> list[str]:
    """Доноры под условиями, которых можно поставить в обход сейчас, — по порядку вкладки, не
    больше `limit`: «Обойти всех по фильтру» ставит ровно их."""
    border = _border(now or datetime.now(UTC))
    suppliers = await supplier_hosts(session, now=now)
    chosen = _where(_base([DomainModel.host], suppliers), query, border).where(_can_start(border))
    rows = await session.execute(chosen.order_by(*_order(query.sort)).limit(limit))
    return [str(host) for (host,) in rows.all()]


async def typical_seconds(session: AsyncSession) -> float:
    """Сколько обычно идёт обход донора — медиана последних законченных, у которых было что
    обходить; замера нет — `TYPICAL_FALLBACK_SEC`. Для «≈ N ч при K обходчиках»."""
    elapsed = CrawlRunModel.stats["elapsed_sec"].as_float()
    rows = await session.execute(
        select(elapsed)
        .where(
            CrawlRunModel.status == CrawlStatus.DONE,
            CrawlRunModel.pages_opened >= TYPICAL_MIN_PAGES,
            elapsed.is_not(None),
        )
        .order_by(CrawlRunModel.id.desc())
        .limit(TYPICAL_SAMPLE)
    )
    seconds = [float(value) for (value,) in rows.all() if value is not None and value > 0]
    return statistics.median(seconds) if seconds else TYPICAL_FALLBACK_SEC
