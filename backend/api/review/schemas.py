"""Схемы экрана рассмотрения прогона."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.api.selection.schemas import MachineView, SellerView
from backend.features.review.candidates import (
    AUTO_ACCEPT_MIN_DECISIONS,
    AUTO_ACCEPT_PRECISION,
    Agreement,
    CandidateRow,
    Decision,
    JudgeAccuracy,
    ReviewPage,
)
from backend.features.review.ordering import Tier


def machine_of(row: CandidateRow) -> MachineView:
    """Вердикт судьи — тот же вид, что на экране «Отбор»: ярлык один."""
    domain = row.domain
    home: dict[str, Any] = domain.judge_home or {}
    return MachineView(
        intent=domain.site_intent,
        recommendation=domain.judge_recommendation,
        decided_by=domain.judge_decided_by,
        quote=domain.judge_quote,
        reason=domain.judge_reason,
        source_url=domain.judge_source_url,
        home_shop=[*(home.get("shop") or []), *(home.get("service") or [])],
        home_reached=home.get("reached") if home else None,
        judged_at=domain.judged_at,
    )


class CandidateCard(BaseModel):
    candidate_id: int
    domain_id: int
    host: str
    status: Decision
    tier: Tier
    #: Решение перенесено из прошлого прогона.
    carried: bool
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    dr: int | None
    org_traffic: int | None
    geo: str | None
    geo_top_share: float | None
    contact_status: str | None
    #: По каким ключам прогона нашёлся домен.
    found_by: list[str]
    #: Почему стоит первым в ярусе: сайт продаёт размещение у себя.
    sells: str | None
    machine: MachineView
    seller: SellerView

    @classmethod
    def of(cls, row: CandidateRow) -> CandidateCard:
        candidate, domain, donor = row.candidate, row.domain, row.donor
        return cls(
            candidate_id=candidate.id,
            domain_id=domain.id,
            host=domain.host,
            status=Decision(candidate.status),
            tier=row.tier,
            carried=candidate.carried,
            decided_by=candidate.decided_by,
            decided_at=candidate.decided_at,
            note=candidate.note,
            dr=donor.dr,
            org_traffic=donor.org_traffic,
            geo=donor.geo,
            geo_top_share=donor.geo_top_share,
            contact_status=donor.contact_status.value if donor.contact_status else None,
            found_by=row.found_by,
            sells=row.sells,
            machine=machine_of(row),
            seller=SellerView(
                answer=domain.seller_answer,
                answered_at=domain.seller_answer_at,
                price=donor.last_price,
                currency=donor.last_price_currency,
            ),
        )


class RunHead(BaseModel):
    id: int
    country: str
    keywords: int
    created_at: datetime


class ReviewView(BaseModel):
    run: RunHead
    rows: list[CandidateCard]
    counts: dict[str, int]
    #: Скрыто под фильтром сомнительных (судья советует отказ).
    hidden: int

    @classmethod
    def of(cls, page: ReviewPage) -> ReviewView:
        run = page.run
        return cls(
            run=RunHead(
                id=run.id,
                country=run.country,
                keywords=len(run.keywords or []),
                created_at=run.created_at,
            ),
            rows=[CandidateCard.of(row) for row in page.rows],
            counts=page.counts,
            hidden=page.hidden,
        )


class DecideBody(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=1000)
    decision: Decision
    note: str | None = Field(default=None, max_length=512)


class DecideResult(BaseModel):
    changed: int
    accepted: int
    #: Поиск контактов поставлен принятым. Пусто — принятых не было.
    contacts_job_id: str | None


class AgreementView(BaseModel):
    advised: int
    agreed: int
    precision: float | None

    @classmethod
    def of(cls, agreement: Agreement) -> AgreementView:
        return cls(
            advised=agreement.advised, agreed=agreement.agreed, precision=agreement.precision
        )


def _table(table: dict[str, Agreement]) -> dict[str, AgreementView]:
    return {key: AgreementView.of(value) for key, value in table.items()}


class AccuracyView(BaseModel):
    """Судья против человека. По этим числам решают, доверять ли судье больше."""

    decided: int
    by_advice: dict[str, AgreementView]
    by_layer: dict[str, dict[str, AgreementView]]
    by_intent: dict[str, dict[str, AgreementView]]
    unjudged: int
    asked_to_review: int
    auto_accept_ready: bool
    auto_accept_precision: float = AUTO_ACCEPT_PRECISION
    auto_accept_min_decisions: int = AUTO_ACCEPT_MIN_DECISIONS

    @classmethod
    def of(cls, accuracy: JudgeAccuracy) -> AccuracyView:
        return cls(
            decided=accuracy.decided,
            by_advice=_table(accuracy.by_advice),
            by_layer={key: _table(value) for key, value in accuracy.by_layer.items()},
            by_intent={key: _table(value) for key, value in accuracy.by_intent.items()},
            unjudged=accuracy.unjudged,
            asked_to_review=accuracy.asked_to_review,
            auto_accept_ready=accuracy.auto_accept_ready,
        )
