"""Качество загрузок: что дала каждая партия — страницы, продающие ссылки и их доля.

Тест-группа 31.10.2026: «за каждую загрузку — дата, способ, итог скана, сколько продающих
ссылок и какой это процент». Строка — загрузка (`donor_loads`): партия файлом или донор вручную.

**Обходы без загрузки — строкой на день.** Обход ставят и кнопкой у донора, которого загрузили
давно или привёл прогон, а загрузки до учёта партий не записаны. Выдумывать им партию нельзя,
прятать тоже: это те же страницы и те же деньги. Обход без загрузки — такой, чей донор не лежал
ни в одной партии к минуте обхода. День — по Москве, как у панели.

**Ночной обход — одной строкой** (11.11.2026). Круг (`night.py`) каждую ночь заново проходит тех же
доноров, и строка на день давала столько одинаковых строк, сколько прошло ночей: их читали
отдельными загрузками и искали в них дубли. Строка одна: итог последней ночи, сколько ночей за ней
и что она дала нового (`night_fresh.py`); прошлые ночи — страницами по запросу (`past_nights`).
Обход круга узнаётся по имени, от которого поставлен (`night.NIGHT_BY` в `requested_by`): строку
круга, которую забрал человек, очередь ставит уже от его имени, и это его обход. В ночную строку
идут все обходы круга, и доноров партий тоже: её вопрос — что сделала ночь. Строка партии при этом
показывает последний обход своих доноров, как и раньше; строкой на день остаются обходы,
поставленные человеком.

**Итог партии — по последнему пригодному обходу каждого её донора** с минуты загрузки: повтор
обновляет итог, а два обхода одного донора не складываются. Обходы другого дня и чужие партии
здесь не считаются.

**Продающая ссылка — ссылка на домен с вердиктом скоринга «куплено»**: это мера обхода, и решение
человека её не меняет. Доля — страницы с такой ссылкой от просканированных, а не ссылки от страниц:
витрина с пятью офферами на каждой странице дала бы 500 %.

**Оба числа лежат на обходе** (`stats`, `gate.selling_stats`), их кладёт пересчёт кандидатов.
Читать страницы из `outlinks` на каждое открытие значило бы проходить сотни тысяч строк, а брать
ссылки у кандидатов нельзя: кандидатов заменит следующий обход донора, и у прошлого дня ссылки
обнулились бы, а страницы остались. У обхода, посчитанного раньше этих чисел, их нет: итог идёт по
тем, у кого есть, а таких нет — он пуст, а не ноль (ноль — «искали и не нашли»). Дополняет их
бесплатно `outreach advertisers --pages-only` (`selling_backfill.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Date,
    Integer,
    Select,
    String,
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
from backend.features.core.models.crawl import (
    CrawlNightModel,
    CrawlRunModel,
    DonorLoadHostModel,
    DonorLoadModel,
)
from backend.features.crawl import board, night_fresh
from backend.features.crawl.gate import SELLING_LINKS, SELLING_PAGES, usable
from backend.features.crawl.night import NIGHT_BY, NightStatus
from backend.features.crawl.panel import ZONE, load_hosts

#: Строк качества и доменов строки на странице: размер называет сервер, экран берёт его из ответа.
PAGE_SIZE = 20
HOSTS_PAGE_SIZE = 50
#: Прошлых ночей за раз — неделя: это раскрытие под ночной строкой, а не вторая таблица.
NIGHTS_PAGE_SIZE = 7

#: Обходы без записи о загрузке, поставленные человеком, — у строки нет партии, есть день.
NO_LOAD = "crawl"
#: Обходы ночного круга: одна строка на все ночи, прошлые — её раскрытием.
NIGHT = "night"


@dataclass(frozen=True, slots=True)
class NightFacts:
    """Чего нет у загрузки: сколько ночей за строкой, идёт ли круг и что нового дала последняя."""

    #: Ночей с обходами круга — вместе с последней.
    nights: int
    #: Круг этой ночи ещё идёт: итог строки не окончательный.
    running: bool
    #: Новых рекламодателей у обходов ночи; пусто — посчитать не по чему (`night_fresh.py`).
    fresh: int | None


@dataclass(frozen=True, slots=True)
class QualityRow:
    """Строка качества: откуда доноры и что дал их последний пригодный обход."""

    at: datetime
    #: `bulk`, `manual`, `NO_LOAD` или `NIGHT`.
    kind: str
    load_id: int | None
    #: День обходов без загрузки или ночь круга; у партии пусто.
    day: date | None
    file_name: str | None
    #: Доменов в строке и, когда он один, — он сам: ручная загрузка видна сразу.
    donors: int
    host: str | None
    #: Доноров с пригодным обходом.
    scanned: int
    pages: int
    #: Продающих ссылок — у обходов, где это число есть; ни у одного нет — пусто.
    selling_links: int | None
    #: Страниц с продающей ссылкой — у тех же обходов.
    selling_pages: int
    #: Доля таких страниц от просканированных; нет числа ни у одного обхода — пусто.
    share: float | None
    #: Только у ночной строки страницы качества; у прошлых ночей пусто.
    night: NightFacts | None = None


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


def _nightly() -> ColumnElement[bool]:
    """Обход ночного круга: поставлен от его имени."""
    return CrawlRunModel.requested_by == NIGHT_BY


def _by_hand() -> ColumnElement[bool]:
    """Обход, поставленный человеком. Имя бывает пустым — консольный замер пишет обход без него
    (`repository.save_crawl`), — поэтому не простое «не равно»: с пустым оно не истинно, и такой
    обход пропал бы из обеих строк."""
    return CrawlRunModel.requested_by.is_distinct_from(NIGHT_BY)


def _run_day() -> ColumnElement[date]:
    """День обхода по Москве. Пояс — текстом запроса, а не параметром: в выборке и группировке
    у параметров разные номера, и база сочла бы их разными выражениями."""
    return cast(func.timezone(literal_column(f"'{ZONE.key}'"), CrawlRunModel.created_at), Date)


def _kind(name: str) -> ColumnElement[str]:
    """Вид строки без партии — текстом запроса: у параметра в выборке с группировкой свой номер."""
    return literal_column(f"'{name}'", String(16))


def _sources() -> Any:
    """Загрузки, дни обходов человека без загрузки и ночная строка — одним списком, чтобы листать
    их вместе. Ночная строка одна: стоит по последнему обходу круга; кругов не было — её нет."""
    loads = select(
        DonorLoadModel.created_at.label("at"),
        DonorLoadModel.kind.label("kind"),
        DonorLoadModel.id.label("load_id"),
        cast(null(), Date).label("day"),
    )
    day = _run_day()
    no_load = cast(null(), Integer).label("load_id")
    days = (
        select(
            func.max(CrawlRunModel.created_at).label("at"),
            _kind(NO_LOAD).label("kind"),
            no_load,
            day.label("day"),
        )
        .where(_without_load(), _by_hand())
        .group_by(day)
    )
    nights = (
        select(
            func.max(CrawlRunModel.created_at).label("at"),
            _kind(NIGHT).label("kind"),
            no_load,
            func.max(day).label("day"),
        )
        .where(_nightly())
        .having(func.count() > 0)
    )
    return union_all(loads, days, nights).subquery("sources")


def _day_runs(kind: str, day: date | None) -> list[ColumnElement[bool]]:
    """Обходы строки без партии: ночи — все обходы круга, дня — поставленные человеком донорам
    без загрузки."""
    if kind == NIGHT:
        return [_nightly(), _run_day() == day]
    return [_without_load(), _by_hand(), _run_day() == day]


def _scope(kind: str, load: DonorLoadModel | None, day: date | None) -> list[ColumnElement[bool]]:
    """Обходы строки качества: донора партии с минуты загрузки, дня или ночи."""
    if load is not None:
        return [
            CrawlRunModel.host.in_(load_hosts(load.id)),
            CrawlRunModel.created_at >= load.created_at,
        ]
    return _day_runs(kind, day)


def _names(kind: str, load_id: int | None, day: date | None) -> Select[tuple[str]]:
    """Домены строки качества: партии — все её, дня и ночи — доноры их обходов."""
    if load_id is not None:
        return select(DonorLoadHostModel.host.label("host")).where(
            DonorLoadHostModel.load_id == load_id
        )
    return select(CrawlRunModel.host.label("host")).where(*_day_runs(kind, day)).distinct()


async def _row(
    session: AsyncSession, at: datetime, kind: str, load: DonorLoadModel | None, day: date | None
) -> QualityRow:
    """Числа одной строки: последний пригодный обход каждого донора и его находки."""
    runs = (
        await session.execute(
            select(
                CrawlRunModel.pages_opened,
                CrawlRunModel.stats[SELLING_PAGES].as_integer(),
                CrawlRunModel.stats[SELLING_LINKS].as_integer(),
            )
            .where(*_scope(kind, load, day), usable())
            .distinct(CrawlRunModel.host)
            .order_by(CrawlRunModel.host, CrawlRunModel.id.desc())
        )
    ).all()
    known = [line for line in runs if line[1] is not None and line[2] is not None]
    known_pages = sum(pages for pages, _, _ in known)
    selling_pages = sum(selling for _, selling, _ in known)
    names = _names(kind, None if load is None else load.id, day).subquery()
    donors, first = (await session.execute(select(func.count(), func.min(names.c.host)))).one()
    return QualityRow(
        at=at,
        kind=kind,
        load_id=None if load is None else load.id,
        day=day,
        file_name=None if load is None else load.file_name,
        donors=int(donors),
        host=first if donors == 1 else None,
        scanned=len(runs),
        pages=sum(pages for pages, _, _ in runs),
        selling_links=sum(links for _, _, links in known) if known else None,
        selling_pages=selling_pages,
        share=selling_pages / known_pages if known_pages else None,
    )


async def _night_facts(session: AsyncSession, day: date) -> NightFacts:
    """Сколько ночей за строкой, идёт ли круг ночи `day` и что нового дали её обходы. Новое — от
    полуночи по Москве: круг начинается с неё (`night.tick`); считается по пригодным обходам ночи —
    по ним пересчёт и заводит кандидатов."""
    nights = await session.scalar(select(func.count(func.distinct(_run_day()))).where(_nightly()))
    status = await session.scalar(
        select(CrawlNightModel.status).where(CrawlNightModel.night == day)
    )
    runs = select(CrawlRunModel.id, CrawlRunModel.host).where(*_day_runs(NIGHT, day), usable())
    return NightFacts(
        nights=int(nights or 0),
        running=status == NightStatus.RUNNING.value,
        fresh=await night_fresh.fresh(
            session, runs, since=datetime.combine(day, time.min, tzinfo=ZONE)
        ),
    )


async def page(session: AsyncSession, *, number: int, limit: int = PAGE_SIZE) -> QualityPage:
    """Страница качества: свежие первыми, порядок полный — до номера партии, дня и вида."""
    sources = _sources()
    total = await session.scalar(select(func.count()).select_from(sources))
    found = await session.execute(
        select(sources.c.at, sources.c.kind, sources.c.load_id, sources.c.day)
        .order_by(
            sources.c.at.desc(),
            sources.c.load_id.desc().nulls_last(),
            sources.c.day.desc(),
            sources.c.kind,
        )
        .limit(limit)
        .offset((number - 1) * limit)
    )
    rows: list[QualityRow] = []
    for at, kind, load_id, day in found.tuples().all():
        load = None if load_id is None else await session.get(DonorLoadModel, load_id)
        row = await _row(session, at, kind, load, day)
        if kind == NIGHT and day is not None:
            row = replace(row, night=await _night_facts(session, day))
        rows.append(row)
    return QualityPage(rows=rows, total=int(total or 0))


async def past_nights(
    session: AsyncSession, *, number: int, limit: int = NIGHTS_PAGE_SIZE
) -> QualityPage:
    """Ночи круга до последней — свежие первыми, каждая итогом своих обходов. Последняя стоит
    строкой страницы качества; числа считает то же правило (`_row`), что и её."""
    day = _run_day()
    nights = (
        select(day.label("day"), func.max(CrawlRunModel.created_at).label("at"))
        .where(_nightly())
        .group_by(day)
        .subquery("nights")
    )
    total = await session.scalar(select(func.count()).select_from(nights))
    found = await session.execute(
        select(nights.c.at, nights.c.day)
        .order_by(nights.c.day.desc())
        .limit(limit)
        .offset(1 + (number - 1) * limit)
    )
    rows = [await _row(session, at, NIGHT, None, night) for at, night in found.tuples().all()]
    return QualityPage(rows=rows, total=max(int(total or 0) - 1, 0))


async def hosts_of(
    session: AsyncSession,
    *,
    load_id: int | None,
    day: date | None,
    night: date | None = None,
    number: int,
    limit: int = HOSTS_PAGE_SIZE,
) -> HostPage:
    """Домены строки качества страницей: партия — все её, день и ночь — доноры их обходов."""
    kind, when = (NIGHT, night) if night is not None else (NO_LOAD, day)
    names = _names(kind, load_id, when).subquery()
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
