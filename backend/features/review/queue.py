"""Очередь прогона: какой бывает строка кандидата и кто в неё ложится.

**В очередь ложится годный по вердикту ЭТОГО прогона** (аудит 20.10.2026), а не по
вердикту домена. Вердикт домена — последнего замера, чьей бы страны он ни был: годный
для прогона США ложился в очередь прогона Канады, отсеянный для Канады не ложился
в очередь Великобритании. Вердикт прогона — его страной и его порогами
(`runs/verdicts.py`): купленного сейчас — замером, свежего — по сохранённым метрикам.

Отдельно от решений (`candidates.py`) с 20.10.2026: модуль решений упёрся в потолок
длины, а строка кандидата и её копия решения нужны обоим — очереди и решению.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.runs.exclusions import not_writing
from backend.features.runs.verdicts import RunVerdicts


class Decision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class QueueReport:
    """Что прогон положил на рассмотрение."""

    pending: int = 0
    #: Решено в прошлых прогонах и перенесено: смотреть заново не нужно.
    carried: dict[str, int] = field(default_factory=dict)
    #: Годны по вердикту, вынесенному до 11.10.2026, но в «кому не пишем» ТЗ
    #: (`runs.exclusions.not_writing`): на рассмотрение не положены.
    not_writing: int = 0


def copy_of(review: str | None) -> dict[str, Any]:
    """Строка домена в прогоне, где о нём не решали: копия решения с пометкой
    «перенесено» — или «предложен», если решения нет. Автора, времени и записки
    у копии нет: они у самого решения и у донора. Одна на очередь и на решение
    в другом прогоне — копии из двух мест не расходятся."""
    decided = review in (Decision.ACCEPTED, Decision.REJECTED)
    return {
        "status": review if decided else Decision.PENDING.value,
        "carried": decided,
        "decided_by": None,
        "decided_at": None,
        "note": None,
    }


async def fill(session: AsyncSession, run: RunModel, hosts: Sequence[str]) -> QueueReport:
    """Положить на рассмотрение годных по вердикту этого прогона доменов.

    Вердикт — записанный прогоном, а у домена без записи — по сохранённым метрикам
    порогами и страной этого прогона (`RunVerdicts.judged`): так кладёт старый прогон
    консоль `outreach review-queue`. Повтор безопасен: пара «прогон + домен» кладётся
    один раз. Решённый домен приходит копией решения (`copy_of`) — той же, что кладёт
    решение в другом прогоне: решение по домену одно.

    Домен из «кому не пишем» не кладётся, даже годный (11.10.2026): крупный сайт вердикт
    прогона и так ставит «не подходит» (`verdict.check_dr`), а здесь — порог действующей
    версии и списки по имени. Сколько таких — в отчёте (`not_writing`).
    """
    verdicts = await RunVerdicts(session).judged(run, hosts)
    suitable = [host for host, verdict in verdicts.items() if verdict.suitable]
    if not suitable:
        return QueueReport()
    rows = await session.execute(
        select(DonorModel.domain_id, DonorModel.review, not_writing())
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DomainModel.host.in_(suitable))
    )
    pending = skipped = 0
    carried: dict[str, int] = {}
    for domain_id, review, off in rows.all():
        if off:
            skipped += 1
            continue
        values = copy_of(review)
        inserted = await session.execute(
            insert(RunCandidateModel)
            .values(run_id=run.id, domain_id=domain_id, **values)
            .on_conflict_do_nothing(constraint="uq_run_candidates_run_domain")
            .returning(RunCandidateModel.id)
        )
        if inserted.scalar_one_or_none() is None:
            continue
        if values["carried"]:
            carried[review] = carried.get(review, 0) + 1
        else:
            pending += 1
    await session.flush()
    return QueueReport(pending=pending, carried=carried, not_writing=skipped)
