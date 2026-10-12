"""Качество агента продаж — под правом `sales`: как люди обходятся с черновиками агента.

`GET /sales/agent-quality?cohort=…` — без записи. Числа считает
`features/sales/agent/calibration.py`, экран собирает `calibration_board.py`: своего счёта
экран не ведёт. Когорта — версия агента (промпты и база знаний); без номера — текущая, а
если она черновиков ещё не писала — последняя писавшая. Незнакомый номер — 404 словами.

Доли экран делит сам из чисел ответа — и только там, где основа не меньше `limits.min_n`;
итог ворот автоответа ситуации и его слова — сервера (`gate`). Флага автоответа здесь нет:
его включает человек.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales.agent import calibration_board as boards
from backend.features.sales.agent.calibration import Agent, Bucket, Cohort, Gate, Tally, Took
from backend.features.sales.agent.replay_drafts import SETTLE

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как воронка.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))


class QualityCounts(BaseModel):
    """Черновики по местам — каждый ровно в одном (`calibration.Bucket`)."""

    #: Решения оператора по готовому черновику — основа долей.
    as_is: int
    edited: int
    rejected: int
    #: Агент молчал («ответ не нужен»): человек тоже промолчал — или ответил.
    true_silence: int
    false_silence: int
    #: Агент отдал человеку: отправлено (с правкой или своё), отклонено, ждёт.
    held_sent: int
    held_rejected: int
    held_waiting: int
    #: Готовый черновик ждёт решения или молчание агента ещё без исхода.
    waiting: int
    #: Ушло автоотправкой — не решение оператора.
    auto: int

    @classmethod
    def of(cls, found: Tally) -> QualityCounts:
        return cls(**{place.value: found[place] for place in Bucket})


class GateView(BaseModel):
    """Ворота автоответа: данных мало, проходит, не проходит — и почему словами."""

    state: Literal["few", "passed", "failed"]
    said: list[str]

    @classmethod
    def of(cls, gate: Gate) -> GateView:
        return cls(state=gate.state.value, said=list(gate.said))


class CohortView(BaseModel):
    """Версия агента: номер для адреса, промпты, таблица ходов и база знаний."""

    id: str
    reply: str | None
    situation: str | None
    judge: str | None
    moves: str | None
    kb: str | None

    @classmethod
    def of(cls, cohort: Cohort) -> CohortView:
        return cls(id=cohort.id, **asdict(cohort))


class CohortRow(BaseModel):
    cohort: CohortView
    #: Это текущая версия агента — по ней пишет агент сейчас.
    current: bool
    #: Когда писались черновики этой версии.
    first_at: datetime
    last_at: datetime
    counts: QualityCounts


class SituationRow(BaseModel):
    """Ситуация письма: её числа и ворота автоответа."""

    #: Метка агента; пусто — письмо отдано человеку до разбора.
    situation: str | None
    title: str
    counts: QualityCounts
    gate: GateView


class ReasonRow(BaseModel):
    """Вид причины отклонения готового черновика — пункт списка, «другое» или «без вида»."""

    reason: str
    count: int


class DiffPart(BaseModel):
    kind: Literal["same", "removed", "added"]
    text: str


class ChangeRow(BaseModel):
    """Правка или отклонение черновика: что написал агент и что ушло."""

    draft_id: int
    thread_id: int | None
    situation: str | None
    title: str
    turn: int | None
    decided_at: datetime | None
    decision: Literal["edited", "rejected"]
    #: Агент отдал черновик человеку (бриф, писатель или судья) — «как есть» было закрыто.
    held: bool
    #: Почему агент отдал человеку — словами брифа, писателя или судьи.
    held_reason: str | None
    #: Почему человек отклонил — его словами.
    reason: str | None
    draft: str
    #: Что ушло — у правки; у отклонения пусто.
    sent: str | None
    #: Разница по словам у правки; пусто — у отклонения или у слишком длинного текста.
    parts: list[DiffPart]

    @classmethod
    def of(cls, change: boards.Change) -> ChangeRow:
        row = change.row
        edited, held = row.took is Took.EDITED, row.agent is Agent.HELD
        return cls(
            draft_id=row.draft_id,
            thread_id=row.thread_id,
            situation=row.situation,
            title=change.title,
            turn=row.turn,
            decided_at=row.decided_at,
            decision="edited" if edited else "rejected",
            held=held,
            held_reason=row.agent_reason if held else None,
            reason=row.reject_reason,
            draft=row.body,
            sent=row.final_body if edited else None,
            parts=[DiffPart(kind=part.kind.value, text=part.text) for part in change.parts],
        )


class QualityLimits(BaseModel):
    """Пороги ворот: N решений и доли процентами (не ниже); молчание человека — исход через
    `settle_days` дней."""

    min_n: int
    as_is: int
    silence: int
    settle_days: int


class AgentQualityView(BaseModel):
    """Экран «Агент: качество»: когорты и показанная когорта целиком."""

    limits: QualityLimits
    #: Версия агента сейчас — даже если черновиков по ней ещё нет.
    current: CohortView
    #: Показанная когорта: её числа ниже.
    shown: CohortView
    #: Когорты журнала, последняя писавшая — первой.
    cohorts: list[CohortRow]
    counts: QualityCounts
    situations: list[SituationRow]
    reasons: list[ReasonRow]
    changes: list[ChangeRow]

    @classmethod
    def of(cls, found: boards.Board) -> AgentQualityView:
        edge = found.limits
        return cls(
            limits=QualityLimits(
                min_n=edge.min_n,
                as_is=edge.as_is,
                silence=edge.silence,
                settle_days=SETTLE.days,
            ),
            current=CohortView.of(found.current),
            shown=CohortView.of(found.shown),
            cohorts=[
                CohortRow(
                    cohort=CohortView.of(line.cohort),
                    current=line.cohort == found.current,
                    first_at=line.first,
                    last_at=line.last,
                    counts=QualityCounts.of(line.found),
                )
                for line in found.cohorts
            ],
            counts=QualityCounts.of(found.found),
            situations=[
                SituationRow(
                    situation=line.situation,
                    title=line.title,
                    counts=QualityCounts.of(line.found),
                    gate=GateView.of(line.gate),
                )
                for line in found.situations
            ],
            reasons=[ReasonRow(reason=kind, count=count) for kind, count in found.reasons],
            changes=[ChangeRow.of(change) for change in found.changes],
        )


@router.get(
    "/agent-quality",
    response_model=AgentQualityView,
    summary="Качество агента продаж: решения людей по черновикам",
)
async def read_quality(
    cohort: str | None = Query(
        default=None, pattern=r"^[0-9a-f]{10}$", description="номер версии агента; пусто — текущая"
    ),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> AgentQualityView:
    try:
        found = await boards.board(session, cohort=cohort, now=datetime.now(UTC))
    except boards.UnknownCohortError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AgentQualityView.of(found)
