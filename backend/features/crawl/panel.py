"""Панель раздела «Рекламодатели»: что стало с донорами выборки — от постановки на обход до
дошедшего письма.

Тест-группа 31.10.2026: от «добавили сто доноров» до «скольким дошло письмо» путь не был виден
одним взглядом: числа жили на вкладках обхода, приёмки и писем, и сложить их мог только человек
с таблицей. Панель отвечает одним окном: сколько доноров поставлено, сколько обходов началось,
сколько рекламодателей получено, у скольких нашёлся адрес, скольким письмо ушло и дошло.

**Выборка одна на все шаги.** Период — обходы, заказанные с начала суток по Москве (`created_at`
строки обхода): «неделя» — семь суток, считая сегодня. Партия — домены одной загрузки
(`crawl/loads.py`) и их обходы с минуты загрузки. Рекламодатели — кандидаты именно этих обходов
(`crawl_run_id`), а не последнего обхода донора вообще: вчерашний обход, ещё не сменённый
сегодняшним, в «сегодня» не считается, иначе число шага было бы не из этой выборки.

**Правила шагов — приёмки** (`acceptance.py`): кто рекламодатель (`writes_to`), кто переведён,
у кого адрес и кому ушло письмо считают те же выражения, поэтому панель и «Приёмка по донорам»
не расходятся. Шаги вложены построением: письмо считается только у домена с адресом, адрес —
только у переведённого. Домен двух доноров — один, поставщик не считается.

**Новый и повторный** — по истории обходов (`outlinks`), а не по кандидатам: кандидаты хранят
только последний обход донора, и ссылка, которую нашли месяц назад, из них уже стёрта. Повторный —
домен встречался в обходе, заказанном раньше выборки, или уже был рекламодателем: сеть партнёрок
лежит в ссылках доменом сети, а рекламодателя называет только её адрес. «Всё время» раньше себя
ничего не знает: повторных там нет, и это сказано на экране.

**Письмо — любое письмо Этапа 2 домену**, и отправленное раньше выборки: домену письмо одно
(`letters/attempts.py`), и новый обход второго ему не шлёт. У повторного рекламодателя это честное
«письмо ему дошло», а не заслуга выборки — поэтому повторные названы рядом.

**Сутки — по Москве**, как часы тревог (`letters/deferrals.py`): «сегодня» человека, а не UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, Subquery, and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import (
    CrawlBacklogModel,
    CrawlRunModel,
    DonorLoadHostModel,
    DonorLoadModel,
    OutLinkModel,
)
from backend.features.crawl.acceptance import (
    Tally,
    addressed_row,
    distinct_domains,
    found_rows,
    not_supplier,
    tally_columns,
    tally_of,
)
from backend.features.crawl.board import ACTIVE
from backend.features.crawl.offer_state import OfferFlags
from backend.features.crawl.suppliers import supplier_hosts
from backend.shared.database.ids import storable

#: Сутки панели — по Москве.
ZONE = ZoneInfo("Europe/Moscow")
#: «Неделя» — столько суток, считая сегодня.
WEEK_DAYS = 7


class Range(StrEnum):
    """Какую выборку смотрят: сегодня, неделю, всё время или одну партию."""

    TODAY = "today"
    WEEK = "week"
    ALL = "all"
    LOAD = "load"


class UnknownLoadError(ValueError):
    """Такой партии нет: номер из адреса устарел или выдуман."""


@dataclass(frozen=True, slots=True)
class Window:
    """Выборка: с какой минуты считать обходы и по какой партии. Пусто — без границы."""

    since: datetime | None = None
    load: DonorLoadModel | None = None


@dataclass(frozen=True, slots=True)
class DonorSteps:
    """Доноры выборки — каждый ровно в одном состоянии, кроме суммы `added`."""

    #: Поставлены на обход: заказан обход, ждут очередь загрузки или лежат в партии.
    added: int = 0
    #: Обход открыл хоть одну страницу.
    started: int = 0
    #: Обход идёт или ждёт в очереди обходов, страниц пока нет.
    running: int = 0
    #: Ждут в очереди загрузки своего обходчика.
    waiting: int = 0
    #: Обход кончился, не открыв ни страницы: сайт закрылся или не пустил.
    refused: int = 0
    #: Домен партии, обхода не было и он не ждёт: без галочки «в обход» или снят с очереди.
    idle: int = 0


@dataclass(frozen=True, slots=True)
class Funnel:
    """Шаги выборки: доноры, рекламодатели и их письма."""

    donors: DonorSteps = field(default_factory=DonorSteps)
    #: Получено, переведено, с адресом, письмо ушло — числа приёмки.
    tally: Tally = field(default_factory=Tally)
    #: Из полученных: домен встретился впервые.
    fresh: int = 0
    #: Из полученных: домен уже встречался раньше выборки.
    repeat: int = 0
    #: Из тех, кому ушло: платформа сообщила о доставке.
    delivered: int = 0


def day_start(moment: datetime) -> datetime:
    """Начало суток по Москве — минутой с поясом."""
    return moment.astimezone(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)


async def window_of(
    session: AsyncSession, chosen: Range, load_id: int | None, *, now: datetime
) -> Window:
    """Выборка по слову экрана. Партии нет — отказ словами: число из адреса не должно молча
    показывать другие цифры."""
    if chosen is Range.TODAY:
        return Window(since=day_start(now))
    if chosen is Range.WEEK:
        return Window(since=day_start(now) - timedelta(days=WEEK_DAYS - 1))
    if chosen is Range.ALL:
        return Window()
    load = (
        await session.get(DonorLoadModel, load_id)
        if load_id is not None and storable(load_id)
        else None
    )
    if load is None:
        raise UnknownLoadError(f"Партии №{load_id} нет: выберите загрузку из списка")
    return Window(since=load.created_at, load=load)


def load_hosts(load_id: int) -> Select[tuple[str]]:
    """Домены партии — подзапросом к обходам, кандидатам и очереди загрузки."""
    return select(DonorLoadHostModel.host).where(DonorLoadHostModel.load_id == load_id)


def in_window(window: Window) -> list[ColumnElement[bool]]:
    """Обход выборки: заказан с `since` и, у партии, — её донора."""
    where: list[ColumnElement[bool]] = []
    if window.since is not None:
        where.append(CrawlRunModel.created_at >= window.since)
    if window.load is not None:
        where.append(CrawlRunModel.host.in_(load_hosts(window.load.id)))
    return where


async def _waiting(session: AsyncSession, window: Window) -> set[str]:
    """Доноры выборки в очереди загрузки: их обход ещё не заведён, а поставлены они уже."""
    query = select(CrawlBacklogModel.host)
    if window.since is not None:
        query = query.where(CrawlBacklogModel.created_at >= window.since)
    if window.load is not None:
        query = query.where(CrawlBacklogModel.host.in_(load_hosts(window.load.id)))
    return set((await session.scalars(query)).all())


async def donor_steps(session: AsyncSession, window: Window) -> DonorSteps:
    """Состояния доноров выборки: начат — сильнее «идёт», «идёт» — сильнее «не открылся»:
    у донора за неделю бывает два обхода, а итог у него один."""
    rows = await session.execute(
        select(
            CrawlRunModel.host,
            func.bool_or(CrawlRunModel.pages_opened > 0),
            func.bool_or(CrawlRunModel.status.in_(ACTIVE)),
        )
        .where(*in_window(window))
        .group_by(CrawlRunModel.host)
    )
    crawled, started, running, refused = set[str](), 0, 0, 0
    for host, opened, active in rows.tuples().all():
        crawled.add(host)
        if opened:
            started += 1
        elif active:
            running += 1
        else:
            refused += 1
    waiting = await _waiting(session, window) - crawled
    if window.load is None:
        return DonorSteps(
            added=len(crawled | waiting),
            started=started,
            running=running,
            waiting=len(waiting),
            refused=refused,
        )
    added = await session.scalar(
        select(func.count())
        .select_from(DonorLoadHostModel)
        .where(DonorLoadHostModel.load_id == window.load.id)
    )
    total = int(added or 0)
    return DonorSteps(
        added=total,
        started=started,
        running=running,
        waiting=len(waiting),
        refused=refused,
        idle=total - len(crawled | waiting),
    )


def seen_before(rows: Subquery, since: datetime | None) -> ColumnElement[bool]:
    """Домен строки встречался раньше выборки: в ссылках обхода или уже рекламодателем. Тем же
    правилом ночная строка качества считает новых рекламодателей ночи (`night_fresh.py`)."""
    if since is None:
        return false()
    linked = (
        select(OutLinkModel.id)
        .join(CrawlRunModel, CrawlRunModel.id == OutLinkModel.crawl_run_id)
        .where(OutLinkModel.target_root == rows.c.root, CrawlRunModel.created_at < since)
        .exists()
    )
    known = (
        select(AdvertiserModel.id)
        .where(AdvertiserModel.domain_id == rows.c.domain_id, AdvertiserModel.created_at < since)
        .exists()
    )
    return or_(linked, known)


def _numbers(window: Window, suppliers: set[str]) -> Select[Any]:
    """Числа рекламодателей выборки — одним запросом над строками приёмки."""
    taken = select(CrawlRunModel.id).where(*in_window(window))
    rows = found_rows(CandidateModel.crawl_run_id.in_(taken)).subquery()
    flags = OfferFlags.build()
    domains = distinct_domains(rows)
    statement = select(
        *tally_columns(rows, flags),
        domains.filter(seen_before(rows, window.since)),
        domains.filter(and_(addressed_row(rows), flags.reached())),
    ).select_from(rows)
    return flags.joined(statement, rows.c.domain_id).where(not_supplier(rows, suppliers))


async def funnel(session: AsyncSession, window: Window) -> Funnel:
    """Панель целиком: доноры выборки, их рекламодатели, адреса и письма."""
    line = (await session.execute(_numbers(window, await supplier_hosts(session)))).one()
    tally = tally_of(line[:4])
    repeat = int(line[4] or 0)
    return Funnel(
        donors=await donor_steps(session, window),
        tally=tally,
        fresh=tally.found - repeat,
        repeat=repeat,
        delivered=int(line[5] or 0),
    )
