"""Ссылки, по которым считаются кандидаты донора: последний обход и вся цепочка обхода 24/7.

До обхода 24/7 кандидаты донора считались по одному последнему пригодному обходу, и новый обход
заменял найденное прежним (`gate._replace_candidates`). Цепочка 24/7 идёт частями по десять тысяч
страниц (`always.py`), ночной обход каждую ночь проходит свежую тысячу (`night.py`): заменяй каждый
обход всё — часть стирала бы находки ночи, а ночь — находки десятков тысяч страниц цепочки.
**Найденное копится** (решение владельца 17.11.2026): кандидаты — по ссылкам последнего пригодного
обхода не из цепочки и всех пригодных частей цепочки донора.

**Страница — у самого нового обхода, где она есть** (`gather`): ссылки страницы, открытой и ночью, и
частью неделю назад, берутся у ночи — на странице сейчас то, что видела она. Части цепочки одних и
тех же страниц не открывают, так что повтор бывает только у обхода не из цепочки. Страница, на
которой новый обход ссылок не нашёл вовсе, в ссылках не видна — её прежние ссылки остаются.

**У каждой страницы — свой обход** (`owner`): по нему кандидат помнит обход своей лучшей ссылки, и
судья с приёмкой находят её строку в `outlinks` (`judge.case_of`, `review.article_dates`).

Отдельно — ссылки самого обхода (`own`) и цепочки целиком (`chain`): по ним меряются продающие
страницы обхода и доля продающих страниц цепочки (`gate.judge_run`). Ссылки читаются колонками, без
объектов строк: у цепочки их сотни тысяч.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlOutcome, CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.links import OutLink

#: Обход, по которому считаются кандидаты: законченный и с открытыми страницами.
USABLE_OUTCOMES = (CrawlOutcome.OK, CrawlOutcome.PARTIAL)

#: Колонки ссылки в порядке полей `OutLink` — строка запроса становится ссылкой как есть.
LINK_COLUMNS = tuple(OutLinkModel.__table__.c[item.name] for item in fields(OutLink))


def usable() -> ColumnElement[bool]:
    """Обход годен кандидатам: законченный, с исходом «обойдён» или «частично»."""
    return (CrawlRunModel.status == CrawlStatus.DONE) & CrawlRunModel.outcome.in_(USABLE_OUTCOMES)


@dataclass(slots=True)
class Union:
    """Ссылки кандидатов донора и откуда каждая страница."""

    #: Для скоринга: у каждой страницы — ссылки самого нового обхода, где она есть.
    links: list[OutLink] = field(default_factory=list)
    #: Страница → обход, чьи ссылки на ней взяты.
    owner: dict[str, int] = field(default_factory=dict)
    #: Ссылки обхода, который судим, — все.
    own: list[OutLink] = field(default_factory=list)
    #: Ссылки частей цепочки 24/7 — все: страницы частей не повторяются.
    chain: list[OutLink] = field(default_factory=list)
    #: В объединении есть части цепочки.
    chained: bool = False


async def runs_of(session: AsyncSession, run: CrawlRunModel) -> list[tuple[int, bool]]:
    """Обходы объединения, новые первыми: судимый, последний пригодный не из цепочки и все пригодные
    части цепочки — номер и часть ли."""
    regular = await session.scalar(
        select(func.max(CrawlRunModel.id)).where(
            usable(), CrawlRunModel.host == run.host, CrawlRunModel.depth_part.is_(None)
        )
    )
    parts = await session.scalars(
        select(CrawlRunModel.id).where(
            usable(), CrawlRunModel.host == run.host, CrawlRunModel.depth_part.is_not(None)
        )
    )
    chain = set(parts.all())
    ids = {run.id, *chain, *([regular] if regular is not None else [])}
    return [(number, number in chain) for number in sorted(ids, reverse=True)]


async def gather(session: AsyncSession, run: CrawlRunModel) -> Union:
    """Ссылки объединения (шапка): обходы — от нового к старому, страница — у первого, где она
    есть."""
    found = Union()
    for number, chained in await runs_of(session, run):
        rows = await session.execute(
            select(*LINK_COLUMNS).where(OutLinkModel.crawl_run_id == number)
        )
        links = [OutLink(*values) for values in rows.tuples().all()]
        found.chained = found.chained or chained
        _take(found, number, links, own=number == run.id, chained=chained)
    return found


def _take(found: Union, number: int, links: list[OutLink], *, own: bool, chained: bool) -> None:
    """Ссылки одного обхода — в объединение: страницу, которую взял обход новее, не берём."""
    for link in links:
        if found.owner.setdefault(link.page_url, number) == number:
            found.links.append(link)
    if own:
        found.own.extend(links)
    if chained:
        found.chain.extend(links)
