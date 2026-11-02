"""Качество загрузок: что дала каждая партия — страницы, продающие ссылки и их доля.

Тест-группа 31.10.2026: «за каждую загрузку — дата, способ, итог скана, сколько продающих
ссылок и какой это процент». Строка — загрузка (`donor_loads`): партия файлом или донор вручную.

**Обходы без загрузки — строкой на день.** Обход ставят и кнопкой у донора, которого загрузили
давно или привёл прогон, а загрузки до учёта партий не записаны. Выдумывать им партию нельзя,
прятать тоже: это те же страницы и те же деньги. Обход без загрузки — такой, чей донор не лежал
ни в одной партии к минуте обхода. День — по Москве, как у панели.

**Итог партии — по последнему пригодному обходу каждого её донора** с минуты загрузки: повтор
обновляет итог, а два обхода одного донора не складываются. Обходы другого дня и чужие партии
здесь не считаются.

**Продающая ссылка — ссылка на домен с вердиктом скоринга «куплено»**: это мера обхода, и решение
человека её не меняет. Ссылок — сумма `links` кандидатов обхода. Доля — страницы с такой ссылкой
от просканированных, а не ссылки от страниц: витрина с пятью офферами на каждой странице дала бы
500 %. Страницы считает пересчёт кандидатов и кладёт в `stats` обхода (`gate.selling_pages`):
читать их из `outlinks` на каждое открытие значило бы проходить сотни тысяч строк. У обхода,
посчитанного раньше этого числа, его нет: доля идёт по тем, у кого есть, а таких нет — она пуста,
а не ноль (ноль — «искали и не нашли»).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Date,
    Integer,
    cast,
    func,
    literal_column,
    null,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, DonorLoadHostModel, DonorLoadModel
from backend.features.crawl import board
from backend.features.crawl.gate import SELLING_PAGES, usable
from backend.features.crawl.panel import ZONE, load_hosts

#: Строк качества и доменов строки на странице: размер называет сервер, экран берёт его из ответа.
PAGE_SIZE = 20
HOSTS_PAGE_SIZE = 50

#: Обходы без записи о загрузке — у строки нет партии, есть день.
NO_LOAD = "crawl"


@dataclass(frozen=True, slots=True)
class QualityRow:
    """Строка качества: откуда доноры и что дал их последний пригодный обход."""

    at: datetime
    #: `bulk`, `manual` или `NO_LOAD`.
    kind: str
    load_id: int | None
    #: День обходов без загрузки; у партии пусто.
    day: date | None
    file_name: str | None
    #: Доменов в строке и, когда он один, — он сам: ручная загрузка видна сразу.
    donors: int
    host: str | None
    #: Доноров с пригодным обходом.
    scanned: int
    pages: int
    selling_links: int
    #: Страниц с продающей ссылкой — у обходов, где это число есть.
    selling_pages: int
    #: Доля таких страниц от просканированных; нет числа ни у одного обхода — пусто.
    share: float | None


@dataclass(frozen=True, slots=True)
class QualityPage:
    rows: list[QualityRow]
    total: int


@dataclass(frozen=True, slots=True)
class HostRow:
    """Домен строки качества: его обход и сколько продающих ссылок он дал."""

    host: str
    crawl: board.CrawlLine | None
    selling_links: int


@dataclass(frozen=True, slots=True)
class HostPage:
    rows: list[HostRow]
    total: int


def _without_load() -> ColumnElement[bool]:
    """Обход без загрузки: его донор не лежал ни в одной партии к минуте обхода."""
    loaded = (
        select(DonorLoadHostModel.load_id)
        .join(DonorLoadModel, DonorLoadModel.id == DonorLoadHostModel.load_id)
        .where(
            DonorLoadHostModel.host == CrawlRunModel.host,
            DonorLoadModel.created_at <= CrawlRunModel.created_at,
        )
    )
    return ~loaded.exists()


def _run_day() -> ColumnElement[date]:
    """День обхода по Москве. Пояс — текстом запроса, а не параметром: в выборке и группировке
    у параметров разные номера, и база сочла бы их разными выражениями."""
    return cast(func.timezone(literal_column(f"'{ZONE.key}'"), CrawlRunModel.created_at), Date)


def _sources() -> Any:
    """Загрузки и дни обходов без загрузки — одним списком, чтобы листать их вместе."""
    loads = select(
        DonorLoadModel.created_at.label("at"),
        DonorLoadModel.id.label("load_id"),
        cast(null(), Date).label("day"),
    )
    day = _run_day()
    days = (
        select(
            func.max(CrawlRunModel.created_at).label("at"),
            cast(null(), Integer).label("load_id"),
            day.label("day"),
        )
        .where(_without_load())
        .group_by(day)
    )
    return union_all(loads, days).subquery("sources")


def _scope(load: DonorLoadModel | None, day: date | None) -> list[ColumnElement[bool]]:
    """Обходы строки качества: донора партии с минуты загрузки или без загрузки в этот день."""
    if load is not None:
        return [
            CrawlRunModel.host.in_(load_hosts(load.id)),
            CrawlRunModel.created_at >= load.created_at,
        ]
    return [_without_load(), _run_day() == day]


async def _row(
    session: AsyncSession, at: datetime, load: DonorLoadModel | None, day: date | None
) -> QualityRow:
    """Числа одной строки: последний пригодный обход каждого донора и его находки."""
    scope = _scope(load, day)
    runs = (
        await session.execute(
            select(
                CrawlRunModel.id,
                CrawlRunModel.pages_opened,
                CrawlRunModel.stats[SELLING_PAGES].as_integer(),
            )
            .where(*scope, usable())
            .distinct(CrawlRunModel.host)
            .order_by(CrawlRunModel.host, CrawlRunModel.id.desc())
        )
    ).all()
    ids = [run for run, _, _ in runs]
    links = await session.scalar(
        select(func.coalesce(func.sum(CandidateModel.links), 0)).where(
            CandidateModel.crawl_run_id.in_(ids), CandidateModel.verdict == Verdict.BOUGHT
        )
    )
    known = [(pages, selling) for _, pages, selling in runs if selling is not None]
    known_pages = sum(pages for pages, _ in known)
    selling_pages = sum(selling for _, selling in known)
    if load is not None:
        hosts = select(DonorLoadHostModel.host).where(DonorLoadHostModel.load_id == load.id)
    else:
        hosts = select(CrawlRunModel.host).where(*scope).distinct()
    names = hosts.subquery()
    donors, first = (await session.execute(select(func.count(), func.min(names.c.host)))).one()
    return QualityRow(
        at=at,
        kind=load.kind if load is not None else NO_LOAD,
        load_id=None if load is None else load.id,
        day=day,
        file_name=None if load is None else load.file_name,
        donors=int(donors),
        host=first if donors == 1 else None,
        scanned=len(runs),
        pages=sum(pages for _, pages, _ in runs),
        selling_links=int(links or 0),
        selling_pages=selling_pages,
        share=selling_pages / known_pages if known_pages else None,
    )


async def page(session: AsyncSession, *, number: int, limit: int = PAGE_SIZE) -> QualityPage:
    """Страница качества: свежие первыми, порядок полный — до номера партии и дня."""
    sources = _sources()
    total = await session.scalar(select(func.count()).select_from(sources))
    found = await session.execute(
        select(sources.c.at, sources.c.load_id, sources.c.day)
        .order_by(sources.c.at.desc(), sources.c.load_id.desc().nulls_last(), sources.c.day.desc())
        .limit(limit)
        .offset((number - 1) * limit)
    )
    rows: list[QualityRow] = []
    for at, load_id, day in found.tuples().all():
        load = None if load_id is None else await session.get(DonorLoadModel, load_id)
        rows.append(await _row(session, at, load, day))
    return QualityPage(rows=rows, total=int(total or 0))


async def hosts_of(
    session: AsyncSession,
    *,
    load_id: int | None,
    day: date | None,
    number: int,
    limit: int = HOSTS_PAGE_SIZE,
) -> HostPage:
    """Домены строки качества страницей: партия — все её, день — доноры его обходов."""
    if load_id is not None:
        hosts = select(DonorLoadHostModel.host.label("host")).where(
            DonorLoadHostModel.load_id == load_id
        )
    else:
        hosts = (
            select(CrawlRunModel.host.label("host"))
            .where(_without_load(), _run_day() == day)
            .distinct()
        )
    names = hosts.subquery()
    total = await session.scalar(select(func.count()).select_from(names))
    shown = list(
        (
            await session.scalars(
                select(names.c.host)
                .order_by(names.c.host)
                .limit(limit)
                .offset((number - 1) * limit)
            )
        ).all()
    )
    links = await session.execute(
        select(CandidateModel.donor_host, func.sum(CandidateModel.links))
        .where(CandidateModel.donor_host.in_(shown), CandidateModel.verdict == Verdict.BOUGHT)
        .group_by(CandidateModel.donor_host)
    )
    selling = {str(host): int(number) for host, number in links.tuples().all()}
    crawls = await board.latest_by_host(session, shown)
    return HostPage(
        rows=[
            HostRow(host=host, crawl=crawls.get(host), selling_links=selling.get(host, 0))
            for host in shown
        ],
        total=int(total or 0),
    )
