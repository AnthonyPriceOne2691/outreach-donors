"""Что отдают маршруты экрана отбора."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.features.core.domain import DonorStatus
from backend.features.donors.selection import (
    HumanIntent,
    SelectionPage,
    SelectionRow,
    SelectionSummary,
    Tab,
)


class MachineView(BaseModel):
    """Что сказал судья и на чём стоял. Цитата и адрес отдаются всегда:
    вердикт без них нельзя проверить, а экран существует ради проверки."""

    intent: str | None
    recommendation: str | None
    decided_by: str | None
    quote: str | None
    reason: str | None
    source_url: str | None
    home_shop: list[str]
    home_reached: bool | None
    judged_at: datetime | None


class HumanView(BaseModel):
    intent: HumanIntent | None
    note: str | None
    decided_at: datetime | None


class SelectionCard(BaseModel):
    """Строка отбора: домен, пороги, судья, человек."""

    domain_id: int
    host: str
    tab: Tab
    donor_id: int | None
    status: DonorStatus | None
    #: Порог и оба числа — «органический трафик 100 ниже 500».
    reject_reason: str | None
    dr: int | None
    org_traffic: int | None
    machine: MachineView
    human: HumanView
    disagrees: bool

    @classmethod
    def of(cls, row: SelectionRow) -> SelectionCard:
        domain, donor = row.domain, row.donor
        home: dict[str, Any] = domain.judge_home or {}
        return cls(
            domain_id=domain.id,
            host=domain.host,
            tab=row.tab,
            donor_id=donor.id if donor else None,
            status=donor.status if donor else None,
            reject_reason=donor.reject_reason if donor else None,
            dr=donor.dr if donor else None,
            org_traffic=donor.org_traffic if donor else None,
            machine=MachineView(
                intent=domain.site_intent,
                recommendation=domain.judge_recommendation,
                decided_by=domain.judge_decided_by,
                quote=domain.judge_quote,
                reason=domain.judge_reason,
                source_url=domain.judge_source_url,
                home_shop=list(home.get("shop") or []),
                home_reached=home.get("reached") if home else None,
                judged_at=domain.judged_at,
            ),
            human=HumanView(
                intent=HumanIntent(domain.human_intent) if domain.human_intent else None,
                note=domain.human_note,
                decided_at=domain.human_verdict_at,
            ),
            disagrees=row.disagrees,
        )


class LayerView(BaseModel):
    """Сходимость слоя судьи с человеком там, где человек смотрел."""

    checked: int
    agreed: int


class SelectionView(BaseModel):
    rows: list[SelectionCard]
    total: int
    tabs: dict[str, int]
    reviewed: int
    disagreements: int
    layers: dict[str, LayerView]

    @classmethod
    def of(cls, page: SelectionPage, summary: SelectionSummary) -> SelectionView:
        return cls(
            rows=[SelectionCard.of(row) for row in page.rows],
            total=page.total,
            tabs=summary.tabs,
            reviewed=summary.reviewed,
            disagreements=summary.disagreements,
            layers={
                key: LayerView(checked=score.checked, agreed=score.agreed)
                for key, score in summary.layers.items()
            },
        )


class DecisionBody(BaseModel):
    """`intent: null` снимает своё решение — на случай, если ошибся."""

    intent: HumanIntent | None
    note: str | None = Field(default=None, max_length=512)
