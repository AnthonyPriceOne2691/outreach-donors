"""Досуд тематики: темы сайтам, записанным до неё, — `outreach topic-backfill`.

Прогон определяет тему новым сайтам рядом с судьёй. У доноров и кандидатов, записанных
раньше, темы нет, и фильтр «Тематика» их не находит. Досуд закрывает этот хвост и стоит
только токенов модели: выдача заново не покупается, метрики Ahrefs не трогаются.

**План — до траты** (`plan_topics`): сколько сайтов, откуда у них текст, токены и деньги
по прайсу модели (`cost.PerSite`). Текст — лестницей, от лучшего:

1. сохранённая выдача прогонов — страница, пришедшая под наш ключ (`runs.candidates.texts`);
2. главная по сети — бесплатно, но это заход на сайт; выключается `--no-home`. Открывается
   и тем, у кого из всего текста — один заголовок главной, сохранённый судом: по одному
   заголовку модель чаще говорит «не понять», а это знание держится месяц;
3. заголовок главной из суда (`domains.judge_home`) — когда главная не открылась или
   открывать её не велено.

**Каждый ответ — в базу сразу** (`step.classify_many`): тема на домен и строка журнала,
короткой фиксацией; перед каждой пачкой (`CHUNK`) — потолок модели. Оборванный досуд теряет
только ответы, которые ещё летят: повтор команды берёт тех, у кого свежей темы нет.

**«Заново» — до момента**, а не всем подряд (`again_before`): определить заново тех, чья тема
записана раньше этого момента. Его печатает план, и повтор после обрыва с тем же моментом
продолжает с места, а не платит второй раз за уже определённых. Сначала — доноры, принятые
человеком: по ним фильтруют чаще всего. Выдуманные домены (`.test`, демо-данные) не трогаются.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Row, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import topics as cfg
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.core.usage import ensure_llm_within_cap
from backend.features.core.usage import record as record_usage
from backend.features.donors.home_signals import check_home
from backend.features.review.candidates import Decision
from backend.features.runs.planning import SerpText, saved_texts
from backend.features.topics.classifier import (
    SOURCE_HOME,
    TOPIC_VERSION,
    SiteText,
    TopicVerdict,
    site_text,
)
from backend.features.topics.cost import PerSite, per_site
from backend.features.topics.step import (
    TOPIC_OPERATION,
    TopicSummary,
    classify_many,
    log_silent,
    record_of,
)
from backend.features.topics.store import TopicStore, is_fresh

#: Зарезервированная зона демо-данных: настоящим сайтом быть не может.
RESERVED_SUFFIX = ".test"

#: Сколько сайтов в пачке: перед каждой — потолок модели.
CHUNK = 40


@dataclass(slots=True)
class TopicPlan:
    """Кому определять тему и откуда текст — до единого вызова модели."""

    hosts: list[str]
    #: Текст есть без захода на сайт: сохранённая выдача прогонов (и заголовок главной).
    texts: dict[str, SiteText]
    #: Откроем главную: текста нет или он — один заголовок главной из суда.
    to_fetch: list[str]
    per_site: PerSite
    #: Заголовок главной из суда — если главная не откроется, тема — по нему.
    fallback: dict[str, SiteText] = field(default_factory=dict)
    #: Из них — доноры, принятые человеком: идут первыми.
    accepted: int = 0
    #: Уже со свежей темой — не трогаются (кроме «заново»).
    fresh: int = 0
    skipped_reserved: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    #: «Заново» — тем, чья тема записана раньше этого момента; `None` — только несвежим.
    again_before: datetime | None = None

    @property
    def asked(self) -> int:
        """Сколько раз позовём модель — верхняя граница: главная может и не открыться."""
        return len(self.texts) + len(self.to_fetch)

    @property
    def without_text(self) -> int:
        """Останутся без темы: текста нет, а главную открывать не велено."""
        return len(self.hosts) - self.asked

    @property
    def tokens(self) -> int:
        return self.asked * self.per_site.tokens

    @property
    def usd(self) -> float:
        return self.per_site.usd(self.asked)


@dataclass(slots=True)
class TopicBackfillReport:
    summary: TopicSummary
    #: Главных, открытых по сети, и не открывшихся.
    fetched: int = 0
    unreached: int = 0
    #: Главная не открылась — тема по заголовку из суда.
    by_title: int = 0
    #: Текста не нашлось нигде — темы нет.
    no_text: int = 0


def _ordered() -> tuple[Any, ...]:
    """Принятые доноры — первыми, затем по DR сверху, затем по имени: порядок полный."""
    accepted = case((DonorModel.review == Decision.ACCEPTED.value, 0), else_=1)
    return (accepted, DonorModel.dr.desc().nullslast(), DomainModel.host)


async def _saved_pages(session: AsyncSession, wanted: set[str]) -> dict[str, SerpText]:
    """Сохранённая выдача прогонов — от свежих к старым: первый найденный текст — свежий."""
    found: dict[str, SerpText] = {}
    runs = await session.execute(select(RunModel.candidates).order_by(RunModel.id.desc()))
    for candidates in runs.scalars().all():
        for host, page in saved_texts(candidates).items():
            if host in wanted and not page.empty:
                found.setdefault(host, page)
    return found


async def _donor_domains(session: AsyncSession, now: datetime) -> list[Row[Any]]:
    """Домены доноров по порядку досуда: свежа ли тема и когда записана, решение человека,
    заголовок главной из суда."""
    rows = await session.execute(
        select(
            DomainModel.host,
            DonorModel.review,
            is_fresh(now, TOPIC_VERSION).label("fresh"),
            DomainModel.topic_at,
            DomainModel.judge_home["title"].astext.label("title"),
        )
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .order_by(*_ordered())
    )
    return list(rows.all())


def _wanted(row: Row[Any], again_before: datetime | None) -> bool:
    """Определять ли: тема несвежая — или «заново», и записана она раньше момента."""
    if not row.fresh:
        return True
    return again_before is not None and row.topic_at is not None and row.topic_at < again_before


def _ladder(
    plan: TopicPlan, chosen: list[Row[Any]], pages: Mapping[str, SerpText], *, fetch_home: bool
) -> None:
    """Текст каждому: выдача прогона; без неё — главная по сети, а заголовок из суда — запасом."""
    for row in chosen:
        text = site_text(pages.get(row.host), None, row.title)
        poor = text.empty or text.source == SOURCE_HOME
        if not poor or (not text.empty and not fetch_home):
            plan.texts[row.host] = text
            plan.by_source[text.source] = plan.by_source.get(text.source, 0) + 1
        elif fetch_home:
            plan.to_fetch.append(row.host)
            if not text.empty:
                plan.fallback[row.host] = text


async def plan_topics(
    session: AsyncSession,
    *,
    limit: int | None = None,
    again_before: datetime | None = None,
    fetch_home: bool = True,
    now: datetime | None = None,
) -> TopicPlan:
    """Домены доноров без свежей темы (и записанные раньше `again_before`), по `_ordered`."""
    every = await _donor_domains(session, now or datetime.now(UTC))
    real = [row for row in every if not row.host.endswith(RESERVED_SUFFIX)]
    wanted = [row for row in real if _wanted(row, again_before)]
    chosen = wanted[:limit] if limit is not None else wanted
    plan = TopicPlan(
        hosts=[row.host for row in chosen],
        texts={},
        to_fetch=[],
        per_site=await per_site(session),
        accepted=sum(1 for row in chosen if row.review == Decision.ACCEPTED.value),
        fresh=sum(1 for row in real if row.fresh and not _wanted(row, again_before)),
        skipped_reserved=len(every) - len(real),
        again_before=again_before,
    )
    pages = await _saved_pages(session, set(plan.hosts))
    _ladder(plan, chosen, pages, fetch_home=fetch_home)
    return plan


async def _homes(client: httpx.AsyncClient, hosts: list[str]) -> dict[str, SiteText]:
    """Главные пачки параллельно — тем же ограничением, что модель."""
    gate = asyncio.Semaphore(cfg.CONCURRENCY)
    found: dict[str, SiteText] = {}

    async def one(host: str) -> None:
        async with gate:
            home = await check_home(client, host)
        text = site_text(None, home)
        if not text.empty:
            found[host] = text

    await asyncio.gather(*(one(host) for host in hosts))
    return found


async def backfill_topics(
    session: AsyncSession,
    plan: TopicPlan,
    *,
    http: httpx.AsyncClient,
    home_client: httpx.AsyncClient | None,
    progress: Callable[[int, int], None] | None = None,
) -> TopicBackfillReport:
    """Темы по плану — пачками; каждый ответ — в базу сразу, вместе со строкой журнала."""
    report = TopicBackfillReport(summary=TopicSummary())
    store = TopicStore(session)

    async def write(host: str, verdict: TopicVerdict) -> None:
        if not verdict.unanswered:
            await store.save({host: record_of(verdict)})
        if verdict.tokens:
            record_usage(session, operation=TOPIC_OPERATION, units=verdict.tokens)
        await session.commit()
        report.summary.record(verdict)

    order = [host for host in plan.hosts if host in plan.texts or host in plan.to_fetch]
    report.no_text = len(plan.hosts) - len(order)
    for start in range(0, len(order), CHUNK):
        chunk = order[start : start + CHUNK]
        texts = await _chunk_texts(plan, chunk, home_client, report)
        if texts:
            await ensure_llm_within_cap(session)
            log_silent(await classify_many(http, texts, on_answer=write))
        if progress is not None:
            progress(min(start + CHUNK, len(order)), len(order))
    return report


async def _chunk_texts(
    plan: TopicPlan,
    chunk: list[str],
    home_client: httpx.AsyncClient | None,
    report: TopicBackfillReport,
) -> dict[str, SiteText]:
    """Тексты пачки: готовые из плана, главные по сети, а не открылась — заголовок из суда."""
    texts = {host: plan.texts[host] for host in chunk if host in plan.texts}
    missing = [host for host in chunk if host not in plan.texts]
    homes = await _homes(home_client, missing) if missing and home_client is not None else {}
    report.fetched += len(homes)
    for host in missing:
        if host in homes:
            texts[host] = homes[host]
            continue
        if home_client is not None:
            report.unreached += 1
        if host in plan.fallback:
            texts[host] = plan.fallback[host]
            report.by_title += 1
        else:
            report.no_text += 1
    return texts
