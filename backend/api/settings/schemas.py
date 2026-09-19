"""Что уходит и приходит по маршрутам порогов и расхода."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from backend.features.core.domain import UsageProvider
from backend.features.core.models.run import RunSettingsModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.spending import Article, Spending
from backend.features.runs.thresholds import Consequences


class ThresholdsBody(BaseModel):
    """Пороги отбора.

    Границы стоят не для красоты: DR выше 90 отсекает всё, кроме
    десятка сайтов мира, и такой прогон стоит юнитов, а даёт ноль.
    """

    min_dr: int = Field(ge=0, le=90)
    min_org_traffic: int = Field(ge=0, le=10_000_000)
    min_refdomains: int = Field(ge=0, le=1_000_000)
    min_keywords: int = Field(ge=0, le=1_000_000)

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


class ThresholdsView(BaseModel):
    """Текущие пороги и история версий.

    `current` пусто, если порогов ещё не заводили: тогда действуют
    умолчания, и они же приходят в `defaults` — чтобы экран не выдумывал
    их на своей стороне.
    """

    current: ThresholdsVersion | None
    defaults: ThresholdsBody
    history: list[ThresholdsVersion]


class ConsequencesView(BaseModel):
    """Последствия новых порогов для базы.

    Показывается обе стороны: и что выпадет, и что вернётся. Порог двигают
    в обе стороны, и «выпадет 340» без «вернётся 12» — половина ответа.
    """

    checked: int
    suitable_now: int
    suitable_after: int
    falls_out: int
    falls_out_with_price: int
    comes_back: int
    unchecked: int

    @classmethod
    def of(cls, data: Consequences) -> ConsequencesView:
        return cls(
            checked=data.checked,
            suitable_now=data.suitable_now,
            suitable_after=data.suitable_after,
            falls_out=data.falls_out,
            falls_out_with_price=data.falls_out_with_price,
            comes_back=data.comes_back,
            unchecked=data.unchecked,
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

    Остаток берётся у Ahrefs, а не считается по своей таблице: ключ общий
    с соседней системой, и наша таблица не видит её трат. Своя таблица
    отвечает на другой вопрос — на что потратили мы.
    """

    since: datetime
    articles: list[ArticleCard]
    units_by_provider: dict[str, int]
    amount_by_provider: dict[str, Decimal]
    total_units: int
    total_amount: Decimal
    ahrefs_left: int | None
    ahrefs_cap: int
    ahrefs_left_error: str | None = None

    @classmethod
    def of(
        cls,
        spending: Spending,
        *,
        ahrefs_left: int | None,
        ahrefs_cap: int,
        error: str | None = None,
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
            ahrefs_left_error=error,
        )
