"""Что уходит и приходит по маршрутам прогона."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.features.core.domain import RunStatus
from backend.features.runs.browse import RunRow
from backend.features.runs.estimate import RunForecast


class RunRequestBody(BaseModel):
    """Чего хотим от прогона. Ключи приходят списком, а не текстом:
    разбор текста в обработчике — это правило, уехавшее в веб-слой."""

    keywords: list[str] = Field(min_length=1, max_length=500)
    country: str = Field(min_length=2, max_length=8)
    depth_pages: int = Field(default=1, ge=1, le=5)


class Forecast(BaseModel):
    """Смета до запуска.

    Числа подписаны как приблизительные не из скромности: точное число
    доменов известно только после выдачи, а выдача — уже трата. Смета
    считает худший случай (все домены новые), а точная проверка идёт
    второй раз, уже по настоящим доменам, до первого платного запроса.
    """

    keywords: int
    depth_pages: int
    expected_results: int
    expected_domains: int
    units_screen: int
    units_metrics: int
    units_by_country: int
    units_total: int
    units_left: int
    units_cap: int
    budget: int
    affordable: bool
    shortfall: int

    @classmethod
    def of(cls, forecast: RunForecast) -> Forecast:
        return cls(
            keywords=forecast.keywords,
            depth_pages=forecast.depth_pages,
            expected_results=forecast.expected_results,
            expected_domains=forecast.expected_domains,
            units_screen=forecast.estimate.screen,
            units_metrics=forecast.estimate.metrics,
            units_by_country=forecast.estimate.by_country,
            units_total=forecast.estimate.total,
            units_left=forecast.units_left,
            units_cap=forecast.units_cap,
            budget=forecast.budget,
            affordable=forecast.affordable,
            shortfall=forecast.shortfall,
        )


class RunCard(BaseModel):
    """Прогон в списке: что запускали и чем кончилось."""

    id: int
    status: RunStatus
    country: str
    keywords: int
    estimated_units: int | None
    actual_units: int | None
    estimate_error: float | None
    stats: dict[str, Any] | None
    started_at: datetime

    @classmethod
    def of(cls, row: RunRow) -> RunCard:
        return cls(
            id=row.run.id,
            status=row.run.status,
            country=row.run.country,
            keywords=len(row.run.keywords),
            estimated_units=row.run.estimated_units,
            actual_units=row.run.actual_units,
            estimate_error=row.estimate_error,
            stats=row.run.stats,
            started_at=row.run.created_at,
        )


class RunQueued(BaseModel):
    """Прогон поставлен в очередь.

    Возвращается номер задачи, а не результат: прогон идёт минутами,
    и держать соединение открытым всё это время значит потерять
    оплаченную работу, если человек закрыл вкладку.
    """

    job_id: str
    note: str = (
        "Прогон встал в очередь. Смета проверяется ещё раз по настоящим доменам "
        "и останавливает прогон до первого платного запроса, если не помещается."
    )
