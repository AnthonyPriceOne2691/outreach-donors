"""Ссылки, по которым считаются кандидаты донора: последний обход и цепочка обхода 24/7.

До обхода 24/7 кандидаты донора считались по одному последнему пригодному обходу, и новый обход
заменял найденное прежним (`gate._replace_candidates`). Цепочка 24/7 идёт частями по десять тысяч
страниц (`always.py`), ночной обход каждую ночь проходит свежую тысячу (`night.py`): заменяй каждый
обход всё — часть стирала бы находки ночи, а ночь — находки десятков тысяч страниц цепочки.
**Найденное копится** (решение владельца 17.11.2026): кандидаты — по ссылкам последнего пригодного
обхода не из цепочки и пригодных частей цепочки донора.

**Ссылок в памяти — не больше потолка** (`CRAWL_ALWAYS_UNION_LINKS`, `runs_of`). Пересчёт держит
ссылки объединения целиком, а у сильного донора цепочка за неделю-две доходит до ста тысяч страниц —
миллионы ссылок и падение задачи по памяти. Поэтому сначала одним запросом считается, сколько ссылок
у каждого обхода, и части берутся от новой к старой, пока сумма не больше потолка; судимый обход и
последний обход не из цепочки берутся всегда. Части старше первой не влезшей **заморожены**
(`Union.frozen`): их ссылки не читаются, а их кандидаты остаются, как были (`gate._replace_candidates`),
— пока тот же домен не найдут части новее.

**Страница — у самого нового обхода, где она есть** (`gather`): ссылки страницы, открытой и ночью, и
частью неделю назад, берутся у ночи — на странице сейчас то, что видела она. Части цепочки одних и
тех же страниц не открывают, так что повтор бывает только у обхода не из цепочки. Страница, на
которой новый обход ссылок не нашёл вовсе, в ссылках не видна — её прежние ссылки остаются.

**У каждой страницы — свой обход** (`owner`): по нему кандидат помнит обход своей лучшей ссылки, и
судья с приёмкой находят её строку в `outlinks` (`judge.case_of`, `review.article_dates`).

**Продающие страницы цепочки — запросом, а не в памяти** (`chain_selling`): разные страницы всех
пригодных частей, где есть ссылка на домен, который сейчас у донора «куплено». Домен ссылки — как его
записал обход: ссылка через сеть партнёрок считается у сети, ссылка на документ — тоже; правило
скоринга (`scoring.advertiser_pages`) сеть отдаёт рекламодателю, а документы не считает, так что на
таких ссылках число запроса может немного разойтись с ним. Мера — для порога «мало рекламодателей».
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, fields

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl_always as always_cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.links import OutLink

logger = logging.getLogger(__name__)

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
    #: У донора есть пригодные части цепочки — прочитанные или замороженные.
    chained: bool = False
    #: Части цепочки, которые не влезли под потолок ссылок: их кандидаты заморожены.
    frozen: set[int] = field(default_factory=set)
    #: Сколько ссылок прочитано.
    loaded: int = 0


@dataclass(frozen=True, slots=True)
class Plan:
    """Какие обходы читать, новые первыми, — номер и часть ли, — и какие части заморожены."""

    runs: list[tuple[int, bool]]
    frozen: set[int]
    #: Ссылок у читаемых обходов — по счёту базы до чтения.
    links: int
    chained: bool


async def _sizes(session: AsyncSession, ids: Sequence[int]) -> dict[int, int]:
    """Ссылок у каждого обхода — одним запросом, до чтения самих ссылок."""
    rows = await session.execute(
        select(OutLinkModel.crawl_run_id, func.count())
        .where(OutLinkModel.crawl_run_id.in_(ids))
        .group_by(OutLinkModel.crawl_run_id)
    )
    return {int(number): int(count) for number, count in rows.tuples().all()}


async def runs_of(session: AsyncSession, run: CrawlRunModel) -> Plan:
    """Обходы объединения (шапка): судимый и последний пригодный не из цепочки — всегда, части — от
    новой к старой, пока ссылок не больше потолка; старше первой не влезшей — заморожены."""
    regular = await session.scalar(
        select(func.max(CrawlRunModel.id)).where(
            usable(), CrawlRunModel.host == run.host, CrawlRunModel.depth_part.is_(None)
        )
    )
    found = await session.scalars(
        select(CrawlRunModel.id)
        .where(usable(), CrawlRunModel.host == run.host, CrawlRunModel.depth_part.is_not(None))
        .order_by(CrawlRunModel.id.desc())
    )
    parts = list(found.all())
    kept = {run.id, *([regular] if regular is not None else [])}
    sizes = await _sizes(session, sorted(kept | set(parts)))
    total = sum(sizes.get(number, 0) for number in kept)
    frozen: set[int] = set()
    for number in parts:
        if number in kept:
            continue
        if frozen or total + sizes.get(number, 0) > always_cfg.UNION_LINKS:
            frozen.add(number)
            continue
        total += sizes.get(number, 0)
        kept.add(number)
    chain = set(parts)
    order = [(number, number in chain) for number in sorted(kept, reverse=True)]
    return Plan(runs=order, frozen=frozen, links=total, chained=bool(chain))


async def gather(session: AsyncSession, run: CrawlRunModel) -> Union:
    """Ссылки объединения (шапка): обходы — от нового к старому, страница — у первого, где она
    есть; замороженные части не читаются."""
    plan = await runs_of(session, run)
    found = Union(chained=plan.chained, frozen=plan.frozen)
    if plan.frozen:
        logger.warning(
            "обход %s (%s): судья читает последние %s ссылок цепочки (потолок %s) — частей "
            "заморожено %s, их кандидаты остаются как были",
            run.host,
            run.id,
            plan.links,
            always_cfg.UNION_LINKS,
            len(plan.frozen),
        )
    for number, _ in plan.runs:
        rows = await session.execute(
            select(*LINK_COLUMNS).where(OutLinkModel.crawl_run_id == number)
        )
        links = [OutLink(*values) for values in rows.tuples().all()]
        found.loaded += len(links)
        _take(found, number, links, own=number == run.id)
    return found


def _take(found: Union, number: int, links: list[OutLink], *, own: bool) -> None:
    """Ссылки одного обхода — в объединение: страницу, которую взял обход новее, не берём."""
    for link in links:
        if found.owner.setdefault(link.page_url, number) == number:
            found.links.append(link)
    if own:
        found.own.extend(links)


async def chain_selling(session: AsyncSession, host: str) -> tuple[int, int]:
    """Продающие страницы и ссылки всех пригодных частей цепочки донора — запросом (шапка): разные
    страницы и ссылки на домены, которые у донора сейчас «куплено». Кандидаты к этой минуте уже
    записаны пересчётом."""
    bought = select(CandidateModel.target_root).where(
        CandidateModel.donor_host == host, CandidateModel.verdict == Verdict.BOUGHT
    )
    parts = select(CrawlRunModel.id).where(
        usable(), CrawlRunModel.host == host, CrawlRunModel.depth_part.is_not(None)
    )
    pages, links = (
        await session.execute(
            select(func.count(func.distinct(OutLinkModel.page_url)), func.count()).where(
                OutLinkModel.crawl_run_id.in_(parts), OutLinkModel.target_root.in_(bought)
            )
        )
    ).one()
    return int(pages), int(links)
