"""Досуд базы, собранной до судьи.

Судья зовётся в прогоне, и у доменов, найденных до него, вердикта нет:
на экране отбора они стоят в «Приняты» только потому, что их никто не
судил — среди них facebook.com и amazon.com. Досуд закрывает этот хвост
и стоит токенов, а не юнитов: метрики этих доменов уже куплены.

**Текст берётся лестницей, от лучшего к худшему:**

1. Сохранённая выдача прогона — бесплатно, та самая страница, что
   пришла под наш ключ.
2. Выдача заново по ключам прогонов, у которых текст не сохранялся, —
   центы у источника выдачи, Ahrefs не трогается. Судья настроен именно
   на такой текст: страница по теме, а не витрина.
3. Поиск `site:домен` — первая позиция, которая не заглавная: та же
   статейная страница, только найденная прямым вопросом. Цент за домен.
4. Главная страница — бесплатно, но хуже: на главной издание со своим
   курсом или магазином выглядит как «продаёт своё» — так 23.09 был
   отрезан marathonhandbook.com. Поэтому по главной модель одна не
   режет: её отказ идёт человеку, режет только правило (корзина,
   услуга на главной). Такие вердикты помечены в причине.

**Выдуманные домены не судятся.** Зона `.test` зарезервирована и
настоящим сайтом быть не может; демонстрационные данные сервиса лежат
именно в ней.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

import httpx
from sqlalchemy import not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import judge as judge_cfg
from backend.features.core import usage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.donors.home_signals import check_home
from backend.features.donors.judging import JudgeSummary, judge_candidates
from backend.features.donors.repository import DonorRepository, JudgeRecord, judged_for_real
from backend.features.runs.pipeline import JUDGE_OPERATION, SERP_OPERATION
from backend.features.runs.planning import SerpText, gather_candidates, saved_texts
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

#: Зарезервированная зона: здесь живут выдуманные домены демо-данных.
RESERVED_SUFFIX = ".test"

#: Сколько доменов судить между записями в базу.
CHUNK = 40

#: Пометка у вердикта, вынесенного по главной, а не по выдаче.
FROM_HOME = "по главной: в выдаче домена не нашлось"


@dataclass(slots=True)
class BackfillPlan:
    """Кого досуживать и откуда у них текст — до единого платного запроса."""

    hosts: list[str]
    saved: dict[str, SerpText]
    #: Прогоны без сохранённого текста: (страна, ключи, глубина).
    searches: list[tuple[str, list[str], int]]
    skipped_reserved: int

    @property
    def keywords_to_search(self) -> int:
        return sum(len(keywords) for _, keywords, _ in self.searches)


@dataclass(slots=True)
class BackfillReport:
    judge: JudgeSummary
    by_source: dict[str, int] = field(default_factory=dict)
    serp_cost_usd: float = 0.0
    no_text: int = 0


async def plan_backfill(
    session: AsyncSession, *, limit: int | None = None, rejudge: bool = False
) -> BackfillPlan:
    """Домены доноров без вердикта судьи и без решения человека.

    Без вердикта — и тот, у кого на домене лишь след сбоя модели
    (`repository.judged_for_real`): модель не ответила, судить надо снова.

    `rejudge` — пересудить и тех, у кого вердикт уже есть: судья улучшился,
    а старые вердикты вынесены прежним. Решение человека не трогается и тут.
    """
    statement = (
        select(DomainModel.host)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(DomainModel.human_intent.is_(None))
        .order_by(DonorModel.dr.desc().nullslast(), DomainModel.host)
    )
    if not rejudge:
        statement = statement.where(or_(DomainModel.judged_at.is_(None), not_(judged_for_real())))
    rows = await session.execute(statement)
    every = [host for (host,) in rows.all()]
    real = [host for host in every if not host.endswith(RESERVED_SUFFIX)]
    hosts = real[:limit] if limit is not None else real

    saved: dict[str, SerpText] = {}
    searches: list[tuple[str, list[str], int]] = []
    runs = await session.execute(select(RunModel).order_by(RunModel.id.desc()))
    for run in runs.scalars().all():
        if not isinstance((run.candidates or {}).get("texts"), dict):
            if run.keywords:
                searches.append((run.country, list(run.keywords), run.depth_pages or 1))
            continue
        for host, text in saved_texts(run.candidates).items():
            # Прогоны идут от свежих к старым: первый найденный текст — свежий.
            saved.setdefault(host, text)
    wanted = set(hosts)
    return BackfillPlan(
        hosts=hosts,
        saved={host: text for host, text in saved.items() if host in wanted},
        searches=searches if wanted - saved.keys() else [],
        skipped_reserved=len(every) - len(real),
    )


async def _search_again(
    provider: SerpProvider, plan: BackfillPlan, missing: set[str]
) -> tuple[dict[str, SerpText], float]:
    found: dict[str, SerpText] = {}
    cost = 0.0
    for country, keywords, depth in plan.searches:
        if not missing - found.keys():
            break
        candidates = await gather_candidates(provider, keywords, country, depth_pages=depth)
        cost += candidates.cost_usd
        for host, text in candidates.texts.items():
            if host in missing and host not in found and not text.empty:
                found[host] = text
    return found, cost


async def _site_search(
    provider: SerpProvider, hosts: Sequence[str]
) -> tuple[dict[str, SerpText], float]:
    """Статейная страница домена прямым вопросом `site:`.

    Заглавная пропускается: на ней издание с магазином сбоку выглядит
    магазином, а судья настроен на страницу по теме.
    """
    if not hosts:
        return {}, 0.0
    before = getattr(provider, "spent", 0.0)
    answers = await provider.search([f"site:{host}" for host in hosts], "us")
    found: dict[str, SerpText] = {}
    for host in hosts:
        for result in answers.get(f"site:{host}", []):
            parsed = urlparse(result.url)
            if parsed.path.strip("/") and (result.title or result.description):
                found[host] = SerpText(
                    url=result.url, title=result.title, description=result.description
                )
                break
    return found, getattr(provider, "spent", 0.0) - before


async def _from_home(client: httpx.AsyncClient, hosts: Sequence[str]) -> dict[str, SerpText]:
    """Главные параллельно: по одной это минуты ожидания на закрытых сайтах."""
    gate = asyncio.Semaphore(judge_cfg.CONCURRENCY)
    found: dict[str, SerpText] = {}

    async def one(host: str) -> None:
        async with gate:
            home = await check_home(client, host)
        if home.reached and (home.title or home.description):
            found[host] = SerpText(
                url=f"https://{host}/",
                title=home.title or None,
                description=home.description or None,
            )

    await asyncio.gather(*(one(host) for host in hosts))
    return found


def _from_home_verdict(record: JudgeRecord) -> JudgeRecord:
    """Вердикт по главной: помечен, и отказ модели понижен до «посмотри».

    Режет по главной только правило — там отказ подтверждён структурой
    (корзина, услуга). Модель одна на главной путает издание со своим
    курсом с магазином.
    """
    reason = f"{FROM_HOME} · {record.reason}"
    if record.recommendation == "reject" and record.decided_by != "rule":
        return replace(record, recommendation="review", reason=f"{reason} · посмотри"[:256])
    return replace(record, reason=reason[:256])


async def backfill(
    session: AsyncSession,
    plan: BackfillPlan,
    *,
    http: httpx.AsyncClient,
    home_client: httpx.AsyncClient,
    provider: SerpProvider | None,
    progress: Callable[[int, int], None] | None = None,
) -> BackfillReport:
    """Досудить по плану и записать вердикты. Метрики не покупаются."""
    texts = dict(plan.saved)
    by_source = {"сохранённая выдача": len(texts)}

    missing = set(plan.hosts) - texts.keys()
    cost = 0.0
    if provider is not None and missing and plan.searches:
        fresh, cost = await _search_again(provider, plan, missing)
        texts.update(fresh)
        by_source["выдача заново"] = len(fresh)
        missing -= fresh.keys()
    if provider is not None and missing:
        direct, spent = await _site_search(provider, sorted(missing))
        cost += spent
        texts.update(direct)
        by_source["поиск site:"] = len(direct)
        missing -= direct.keys()

    if cost:
        # Выдача оплачена сейчас, а не в конце: журнал не должен зависеть
        # от того, дойдёт ли досуд до последней пачки.
        usage.record(session, operation=SERP_OPERATION, amount_usd=cost)
        await session.commit()

    homes = await _from_home(home_client, sorted(missing))
    texts.update(homes)
    by_source["главная"] = len(homes)

    judged = [host for host in plan.hosts if host in texts]
    summary = JudgeSummary()
    repository = DonorRepository(session)
    # Пачками, с записью после каждой: 23.09 пересуд 385 доменов шёл полчаса
    # и писал всё одним куском в конце — упади он на середине, оплаченные
    # токены пропали бы вместе с вердиктами, а хода работы не было видно.
    for start in range(0, len(judged), CHUNK):
        chunk = judged[start : start + CHUNK]
        outcome = await judge_candidates(
            http,
            chunk,
            texts,
            already_judged={},
            paid=[],
            home_client=home_client,
            index=provider,
        )
        await repository.save_judgements(
            {
                host: _from_home_verdict(record) if host in homes else record
                for host, record in outcome.verdicts.items()
            }
        )
        if outcome.summary.tokens:
            usage.record(session, operation=JUDGE_OPERATION, units=outcome.summary.tokens)
        if outcome.summary.index_usd:
            usage.record(session, operation=SERP_OPERATION, amount_usd=outcome.summary.index_usd)
        await session.commit()
        summary.merge(outcome.summary)
        if progress is not None:
            progress(min(start + CHUNK, len(judged)), len(judged))
    return BackfillReport(
        judge=summary,
        by_source=by_source,
        serp_cost_usd=cost,
        no_text=len(plan.hosts) - len(judged),
    )
