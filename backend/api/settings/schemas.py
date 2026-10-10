"""Что уходит и приходит по маршрутам порогов и расхода."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from backend.features.core.domain import UsageProvider
from backend.features.core.models.run import RunSettingsModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.spending import Article, Spending
from backend.features.runs.thresholds import Consequences

#: Допустимые пороги — целые от и до, включительно. Одно место на схему и
#: экран: схема ниже отказывает по этим числам, а экран узнаёт их из ответа
#: (`ThresholdsView.limits`) и отказывает до нажатия тем же правилом
#: (замечание 28.09.2026: «валидация на допустимые значения и значки
#: подсказок, в каких диапазонах»). До этого экран знал только верх DR,
#: а трафик в сто миллионов уходил на сервер и возвращался отказом
#: по-английски.
#:
#: Границы стоят не для красоты: DR выше 90 отсекает всё, кроме десятка
#: сайтов мира, и такой прогон стоит юнитов, а даёт ноль.
LIMITS: dict[str, tuple[int, int]] = {
    "min_dr": (0, 90),
    "min_org_traffic": (0, 10_000_000),
    "min_refdomains": (0, 1_000_000),
    "min_keywords": (0, 1_000_000),
}


def _within(name: str) -> Any:
    low, high = LIMITS[name]
    return Field(ge=low, le=high)


class ThresholdsBody(BaseModel):
    """Пороги отбора — в границах `LIMITS`."""

    min_dr: int = _within("min_dr")
    min_org_traffic: int = _within("min_org_traffic")
    min_refdomains: int = _within("min_refdomains")
    min_keywords: int = _within("min_keywords")

    def to_thresholds(self) -> Thresholds:
        return Thresholds(
            min_dr=self.min_dr,
            min_org_traffic=self.min_org_traffic,
            min_refdomains=self.min_refdomains,
            min_keywords=self.min_keywords,
        )


class ThresholdsVersion(BaseModel):
    """Версия порогов: что стояло, кто поставил и когда."""

    version: int
    created_by: str | None
    created_at: datetime
    min_dr: int
    min_org_traffic: int
    min_refdomains: int
    min_keywords: int

    @classmethod
    def of(cls, settings: RunSettingsModel) -> ThresholdsVersion:
        return cls(
            version=settings.version,
            created_by=settings.created_by,
            created_at=settings.created_at,
            min_dr=settings.min_dr,
            min_org_traffic=settings.min_org_traffic,
            min_refdomains=settings.min_refdomains,
            min_keywords=settings.min_keywords,
        )


class Range(BaseModel):
    """Допустимые значения порога: целые от `min` до `max` включительно."""

    min: int
    max: int


def _limits() -> dict[str, Range]:
    return {name: Range(min=low, max=high) for name, (low, high) in LIMITS.items()}


class ThresholdsView(BaseModel):
    """Текущие пороги и история версий.

    `current` пусто, если порогов ещё не заводили: тогда действуют
    умолчания, и они же приходят в `defaults` — чтобы экран не выдумывал
    их на своей стороне. Так же и границы (`limits`): экран проверяет
    поле ими, а не своей копией чисел.
    """

    current: ThresholdsVersion | None
    defaults: ThresholdsBody
    history: list[ThresholdsVersion]
    limits: dict[str, Range] = Field(default_factory=_limits)


class ConsequencesView(BaseModel):
    """Эти пороги против действующих — по метрикам доменов базы.

    Показываются обе стороны: и кого эти пороги отсекут, и кого пропустят сверх
    действующих. Порог двигают в обе стороны, и «отсекут 340» без «пропустят 12» —
    половина ответа. Вердикты в базе сохранение не переписывает, и имена полей
    не обещают перемен в ней: до 10.10.2026 они звались «выпадет из базы» и
    «вернётся в базу» (проверка прода 10.10.2026).
    """

    #: Домены с метриками: их и сравнивают.
    checked: int
    #: Пропускают действующие пороги.
    passing_now: int
    #: Пропустят эти.
    passing_after: int
    #: Действующие пропускают, эти — нет.
    cut: int
    #: Из них — с полученной ценой.
    cut_with_price: int
    #: Эти пропускают, действующие — нет.
    admitted: int
    #: Действующие отсекают, эти пустили бы дальше, но метрик для решения нет.
    undecided: int
    #: Домены без метрик: пороги их не судят.
    without_metrics: int

    @classmethod
    def of(cls, data: Consequences) -> ConsequencesView:
        return cls(
            checked=data.checked,
            passing_now=data.passing_now,
            passing_after=data.passing_after,
            cut=data.cut,
            cut_with_price=data.cut_with_price,
            admitted=data.admitted,
            undecided=data.undecided,
            without_metrics=data.without_metrics,
        )


class ArticleCard(BaseModel):
    """Статья расхода."""

    provider: UsageProvider
    operation: str
    units: int
    amount_usd: Decimal
    calls: int

    @classmethod
    def of(cls, article: Article) -> ArticleCard:
        return cls(
            provider=article.provider,
            operation=article.operation,
            units=article.units,
            amount_usd=article.amount_usd,
            calls=article.calls,
        )


class SpendingView(BaseModel):
    """Расход с начала месяца плюс остаток у провайдера.

    Три числа, и путать их нельзя — первая версия экрана это и делала,
    показывая «израсходовано 0» при шести тысячах потраченных юнитов:

    * `ahrefs_left` — остаток **у провайдера**. Ключ общий с соседней
      системой, поэтому её траты тоже уменьшают это число;
    * `ahrefs_cap` — наш добровольный потолок на месяц;
    * `ahrefs_spent_by_us` — сколько потратили мы, по своей таблице.
      Именно это число сравнивают с капом; остаток провайдера для этого
      не годится, потому что включает чужой расход.
    """

    since: datetime
    articles: list[ArticleCard]
    units_by_provider: dict[str, int]
    amount_by_provider: dict[str, Decimal]
    total_units: int
    total_amount: Decimal
    ahrefs_left: int | None
    ahrefs_cap: int
    ahrefs_spent_by_us: int
    ahrefs_left_error: str | None = None
    #: Остаток денег у источника выдачи. Ключ там свой, не общий,
    #: поэтому вычитать чужое не из чего.
    serp_left_usd: Decimal | None = None
    serp_spent_by_us: Decimal = Decimal(0)
    serp_left_error: str | None = None
    #: Расход продаж не показан — у учётки нет права «Продажи» (решение Anthony 10.10.2026,
    #: П2б): статьи, суммы по провайдерам и итог — без него и сходятся между собой. Экран
    #: говорит это словами: итог без продаж — не весь счёт.
    sales_hidden: bool = False

    @classmethod
    def of(
        cls,
        spending: Spending,
        *,
        ahrefs_left: int | None,
        ahrefs_cap: int,
        error: str | None = None,
        serp_left_usd: Decimal | None = None,
        serp_left_error: str | None = None,
        sales_hidden: bool = False,
    ) -> SpendingView:
        return cls(
            since=spending.since,
            articles=[ArticleCard.of(article) for article in spending.articles],
            units_by_provider={
                provider.value: units for provider, units in spending.units_by_provider.items()
            },
            amount_by_provider={
                provider.value: amount for provider, amount in spending.amount_by_provider.items()
            },
            total_units=spending.total_units,
            total_amount=spending.total_amount,
            ahrefs_left=ahrefs_left,
            ahrefs_cap=ahrefs_cap,
            ahrefs_spent_by_us=spending.units_by_provider.get(UsageProvider.AHREFS, 0),
            ahrefs_left_error=error,
            serp_left_usd=serp_left_usd,
            serp_spent_by_us=spending.amount_by_provider.get(UsageProvider.SERP, Decimal(0)),
            serp_left_error=serp_left_error,
            sales_hidden=sales_hidden,
        )
