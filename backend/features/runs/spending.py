"""Расход: сколько потрачено и сколько осталось.

Правило, ради которого этот модуль существует: **остаток не хранится
полем.** Два независимых счётчика неизбежно разойдутся, и разойдутся
незаметно — остаток считается как сумма строк расхода, а по Ahrefs
вдобавок спрашивается у самого провайдера.

**Своя таблица и остаток провайдера отвечают на разные вопросы.**
Таблица знает, на что мы потратили; провайдер знает, сколько осталось
на ключе, общем с соседней системой. Судить об остатке по своей таблице
нельзя — она не видит чужих трат.

**Расход продаж — только с правом «Продажи»** (решение Anthony 10.10.2026, П2б).
Без права операций продаж (`usage.SALES_OPERATIONS`) нет ни в статьях, ни в суммах
по провайдерам, ни в итоге: суммы сходятся со статьями, и расход продаж не
вычитается из итога. Что итог — без продаж, экран говорит словами. Кап Ahrefs и
потолки считают весь расход (`EVERY_STAGE`): этапы видимости — про экран, а не про деньги.

**Этап — у каждой статьи**: расход Этапа 2 считается отдельно от Этапа 1. Одна
операция у разных этапов — разные статьи: DR кандидатов Этапа 2 и метрики
прогона Этапа 1 — обе `batch_metrics`, а на экране — две строки. Разбивка по этапам
(`Spending.by_stage`) единицы по провайдерам не складывает: у них разная валюта счёта.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy import true as sa_true
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.usage import SALES_OPERATIONS
from backend.features.outreach.repository import EVERY_STAGE

#: Провайдеры платного обхода Этапа 2 — то, что держит свой потолок Этапа 2.
CRAWL_PROVIDERS = (UsageProvider.PROXY, UsageProvider.RENDER)

#: Порядок этапов в разбивке; трата без этапа — последней.
STAGE_ORDER: tuple[Stage | None, ...] = (*Stage, None)


@dataclass(frozen=True, slots=True)
class Article:
    """Строка расхода: провайдер, операция, этап, сколько и на сколько."""

    provider: UsageProvider
    operation: str
    units: int
    amount_usd: Decimal
    calls: int
    #: Этап траты; `None` — общая на все этапы или этап не назван (`core/usage.py`).
    stage: Stage | None = None


@dataclass(frozen=True, slots=True)
class StageSpending:
    """Расход одного этапа: единицы по провайдерам и деньги.

    Единицы в одно число не складываются: юниты, токены, письма и байты — разные валюты,
    и сумма вышла бы числом, которого нет ни в одном счёте.
    """

    stage: Stage | None
    units_by_provider: dict[UsageProvider, int]
    amount_usd: Decimal
    calls: int


@dataclass(frozen=True, slots=True)
class Spending:
    """Расход за период плюс то, что о нём знает провайдер."""

    since: datetime
    articles: list[Article]
    units_by_provider: dict[UsageProvider, int]
    amount_by_provider: dict[UsageProvider, Decimal]

    @property
    def total_units(self) -> int:
        return sum(self.units_by_provider.values())

    @property
    def total_amount(self) -> Decimal:
        return sum(self.amount_by_provider.values(), Decimal(0))

    @property
    def by_stage(self) -> list[StageSpending]:
        """Разбивка по этапам — только у этапов с тратами, в порядке `STAGE_ORDER`."""
        result: list[StageSpending] = []
        for stage in STAGE_ORDER:
            mine = [article for article in self.articles if article.stage is stage]
            if not mine:
                continue
            result.append(
                StageSpending(
                    stage=stage,
                    units_by_provider=_sum_units(mine),
                    amount_usd=sum((article.amount_usd for article in mine), Decimal(0)),
                    calls=sum(article.calls for article in mine),
                )
            )
        return result


def _month_start(now: datetime | None = None) -> datetime:
    """Лимиты провайдеров месячные, и расход считается с первого числа:
    «за последние 30 дней» отвечало бы на другой вопрос."""
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _seen(stages: Collection[Stage]) -> ColumnElement[bool]:
    """Расход видимых этапов: без права «Продажи» — без операций продаж и без строк, которые
    вызов назвал тратой продаж (П2б). Строка без этапа продажам не принадлежит: отправка
    письма — общая на все этапы."""
    if Stage.SALES in stages:
        return sa_true()
    return UsageRecordModel.operation.not_in(
        sorted(SALES_OPERATIONS)
    ) & UsageRecordModel.stage.is_distinct_from(Stage.SALES)


class SpendingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def since_month_start(
        self, *, stages: Collection[Stage], now: datetime | None = None
    ) -> Spending:
        """Расход с первого числа — этапов, которые видит спрашивающий (`stages`). Умолчания
        у этапов нет (ревью продаж к #304): забытый вызов ловит mypy."""
        since = _month_start(now)
        articles = await self._articles(
            UsageRecordModel.created_at >= since, _seen(stages), by_stage=True
        )
        return Spending(
            since=since,
            articles=articles,
            units_by_provider=_sum_units(articles),
            amount_by_provider=_sum_amount(articles),
        )

    async def of_run(self, run_id: int, *, stages: Collection[Stage]) -> list[Article]:
        """Расход прогона по журналу — статьями, всё записанное за его номером.

        По журналу, а не по отчёту прогона: журнал пишется на каждом чекпоинте и знает
        выдачу в деньгах, токены судьи и траты прежних попыток (`runs/outcome.py`).
        Этапы видимости — то же правило, что у экрана расхода (П2б): у прогона доноров
        операций продаж нет, но правило не должно держаться на том, что их пока нет.
        """
        return await self._articles(UsageRecordModel.run_id == run_id, _seen(stages))

    async def _articles(self, *where: ColumnElement[bool], by_stage: bool = False) -> list[Article]:
        """Статьи расхода по условию: провайдер и операция (`by_stage` — и этап), сумма единиц
        и денег, запросов.

        Порядок — по единицам, при равных — по провайдеру и операции: строки с одной суммой
        иначе вставали бы как придётся, от ответа к ответу по-разному. Этап делит строки на
        экране «Расход» — одна операция у двух этапов — две строки; отчёту прогона он не
        нужен: прогон — один этап.
        """
        keys: list[Any] = [UsageRecordModel.provider, UsageRecordModel.operation]
        if by_stage:
            keys.append(UsageRecordModel.stage)
        units = func.coalesce(func.sum(UsageRecordModel.units), 0)
        rows = await self._session.execute(
            select(
                units,
                func.coalesce(func.sum(UsageRecordModel.amount_usd), 0),
                func.count(UsageRecordModel.id),
                *keys,
            )
            .where(*where)
            .group_by(*keys)
            .order_by(units.desc(), *keys)
        )
        # `coalesce` в запросе уже подставил ноль вместо пустоты, но
        # типизатор об этом не знает: для него сумма по столбцу всегда
        # может быть `None`. Подстраховка ниже — для него, а не от жизни.
        return [
            Article(
                provider=key[0],
                operation=key[1],
                units=int(spent or 0),
                amount_usd=Decimal(amount or 0),
                calls=int(calls),
                stage=key[2] if by_stage else None,
            )
            for spent, amount, calls, *key in rows.tuples().all()
        ]


async def ahrefs_spent_this_month(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Сколько юнитов Ahrefs потратили мы с начала месяца.

    По своей таблице, а не по остатку провайдера: остаток включает траты
    соседней системы на общем ключе и на вопрос «сколько съели мы»
    не отвечает.
    """
    # Кап — про весь наш расход: этапы видимости здесь не сужают ничего.
    spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE, now=now)
    return spending.units_by_provider.get(UsageProvider.AHREFS, 0)


async def cap_left(session: AsyncSession, *, cap: int, now: datetime | None = None) -> int:
    """Сколько юнитов осталось по нашему добровольному капу в этом месяце.

    **Кап месячный, и вычитать из него потраченное обязательно.** Без
    этого он работает потолком одного прогона: потратив девяносто тысяч
    из ста, следующий прогон снова видит все сто — и кап, названный
    месячным на экране расхода, в бюджете прогона означает совсем
    другое. Одно число с двумя смыслами расходится молча.

    Считается по своей таблице, а не по остатку провайдера: остаток
    включает траты соседней системы на общем ключе, а наш кап —
    про нас.
    """
    return max(0, cap - await ahrefs_spent_this_month(session, now=now))


async def crawl_spent_this_month(
    session: AsyncSession,
    *,
    stages: Collection[Stage] = EVERY_STAGE,
    now: datetime | None = None,
) -> Decimal:
    """Сколько денег ушло с начала месяца на платный обход Этапа 2 — прокси и рендеринг.

    С этим числом сравнивается свой потолок Этапа 2 (`crawl/spend.py`, все этапы) и его же
    показывает экран «Расход» — тем же правилом видимости, что статьи (`_seen`): одно
    число — одно место. Строка без денег (цена не задана) сюда не входит — под потолком
    такую трату платный уровень и не делает.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageRecordModel.amount_usd), 0)).where(
            UsageRecordModel.provider.in_(CRAWL_PROVIDERS),
            UsageRecordModel.created_at >= _month_start(now),
            _seen(stages),
        )
    )
    return Decimal(total or 0)


def _sum_units(articles: Sequence[Article]) -> dict[UsageProvider, int]:
    totals: dict[UsageProvider, int] = {}
    for article in articles:
        totals[article.provider] = totals.get(article.provider, 0) + article.units
    return totals


def _sum_amount(articles: Sequence[Article]) -> dict[UsageProvider, Decimal]:
    totals: dict[UsageProvider, Decimal] = {}
    for article in articles:
        totals[article.provider] = totals.get(article.provider, Decimal(0)) + article.amount_usd
    return totals
