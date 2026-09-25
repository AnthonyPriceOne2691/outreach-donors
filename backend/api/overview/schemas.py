"""Что отдаёт главная."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from backend.api.letters.schemas import Transport
from backend.api.runs.schemas import RunCard
from backend.features.core.domain import Stage
from backend.features.ops.overview import DonorCounts, LetterCounts, Overview, Waiting


class DonorsCard(BaseModel):
    """Доноры по ходу работы — независимые числа, не воронка."""

    total: int
    unchecked: int
    suitable: int
    accepted: int
    rejected: int
    with_email: int
    form_only: int
    written: int
    replied: int
    priced: int
    priced_fresh: int

    @classmethod
    def of(cls, counts: DonorCounts) -> DonorsCard:
        return cls(
            total=counts.total,
            unchecked=counts.unchecked,
            suitable=counts.suitable,
            accepted=counts.accepted,
            rejected=counts.rejected,
            with_email=counts.with_email,
            form_only=counts.form_only,
            written=counts.written,
            replied=counts.replied,
            priced=counts.priced,
            priced_fresh=counts.priced_fresh,
        )


class WaitingCard(BaseModel):
    """Что без человека не сдвинется."""

    review: int
    review_runs: list[int]
    prices: int
    leads: int
    forms: int
    advertisers: int

    @classmethod
    def of(cls, waiting: Waiting) -> WaitingCard:
        return cls(
            review=waiting.review,
            review_runs=waiting.review_runs,
            prices=waiting.prices,
            leads=waiting.leads,
            forms=waiting.forms,
            advertisers=waiting.advertisers,
        )


class LettersCard(BaseModel):
    queued: int
    sent: int
    delivered: int
    bounced: int

    @classmethod
    def of(cls, counts: LetterCounts) -> LettersCard:
        return cls(
            queued=counts.queued,
            sent=counts.sent,
            delivered=counts.delivered,
            bounced=counts.bounced,
        )


class OverviewView(BaseModel):
    """Главная целиком: где мы и что ждёт человека."""

    donors: DonorsCard
    waiting: WaitingCard
    letters: dict[Stage, LettersCard]
    #: Карточка той же формы, что строка истории прогонов: одни числа
    #: под двумя формами на двух экранах разошлись бы.
    last_run: RunCard | None
    ahrefs_units: int
    ahrefs_cap: int
    serp_usd: Decimal
    #: Почта: «не подключена» — не поломка, а состояние до рабочего сервера.
    transport: Transport

    @classmethod
    def of(cls, overview: Overview, *, transport: Transport) -> OverviewView:
        return cls(
            donors=DonorsCard.of(overview.donors),
            waiting=WaitingCard.of(overview.waiting),
            letters={stage: LettersCard.of(counts) for stage, counts in overview.letters.items()},
            last_run=RunCard.of(overview.last_run) if overview.last_run else None,
            ahrefs_units=overview.ahrefs_units,
            ahrefs_cap=overview.ahrefs_cap,
            serp_usd=overview.serp_usd,
            transport=transport,
        )
