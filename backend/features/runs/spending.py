"""Расход: сколько потрачено и сколько осталось.

Правило, ради которого этот модуль существует: **остаток не хранится
полем.** Два независимых счётчика неизбежно разойдутся, и разойдутся
незаметно — остаток считается как сумма строк расхода, а по Ahrefs
вдобавок спрашивается у самого провайдера.

**Своя таблица и остаток провайдера отвечают на разные вопросы.**
Таблица знает, на что мы потратили; провайдер знает, сколько осталось
на ключе, общем с соседней системой. Судить об остатке по своей таблице
нельзя — она не видит чужих трат.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel


@dataclass(frozen=True, slots=True)
class Article:
    """Строка расхода: провайдер, операция, сколько и на сколько."""

    provider: UsageProvider
    operation: str
    units: int
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


def _month_start(now: datetime | None = None) -> datetime:
    """Лимиты провайдеров месячные, и расход считается с первого числа:
    «за последние 30 дней» отвечало бы на другой вопрос."""
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class SpendingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def since_month_start(self, *, now: datetime | None = None) -> Spending:
        since = _month_start(now)
        rows = await self._session.execute(
            select(
                UsageRecordModel.provider,
                UsageRecordModel.operation,
                func.coalesce(func.sum(UsageRecordModel.units), 0),
                func.coalesce(func.sum(UsageRecordModel.amount_usd), 0),
                func.count(UsageRecordModel.id),
            )
            .where(UsageRecordModel.created_at >= since)
            .group_by(UsageRecordModel.provider, UsageRecordModel.operation)
            .order_by(func.coalesce(func.sum(UsageRecordModel.units), 0).desc())
        )

        # `coalesce` в запросе уже подставил ноль вместо пустоты, но
        # типизатор об этом не знает: для него сумма по столбцу всегда
        # может быть `None`. Подстраховка ниже — для него, а не от жизни.
        articles = [
            Article(
                provider=provider,
                operation=operation,
                units=int(units or 0),
                amount_usd=Decimal(amount or 0),
                calls=int(calls),
            )
            for provider, operation, units, amount, calls in rows.tuples().all()
        ]
        return Spending(
            since=since,
            articles=articles,
            units_by_provider=_sum_units(articles),
            amount_by_provider=_sum_amount(articles),
        )


async def ahrefs_spent_this_month(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Сколько юнитов Ahrefs потратили мы с начала месяца.

    По своей таблице, а не по остатку провайдера: остаток включает траты
    соседней системы на общем ключе и на вопрос «сколько съели мы»
    не отвечает.
    """
    spending = await SpendingRepository(session).since_month_start(now=now)
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
