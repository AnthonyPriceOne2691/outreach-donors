"""Принятый человеком донор — чей вердикт его понижает.

Ревью 20.10.2026: адреса, письма, добивки и перезапрос цены смотрят на вердикт донора
(`donors.status`), а замер писал его поверх любого — своей страной и порогами. Принятый в
прогоне Великобритании, через 91 день перемеренный прогоном Канады, становился «не подходит»
при `review = accepted`, и всё, что ему положено, молча вставало.

**Замер пишет донору два вердикта** (`DonorRepository.save_results`). Свой — вердикт замера
(`measured_status`, и строкой прогона в `run_verdicts`). Для выходов — `status`: у
непринятого он тот же, а принятому его понижает только вердикт по стране и порогам прогона,
где его приняли, — по новым данным замера (`rejudge`). Судить им новые данные нечем (замер
другой страны не купил стран или метрик) — статус прежний: «нечем судить» не отказ.

**Принятый без прогона — годен словом человека**: заведённый руками (`manual_price.py`) и
липовый (`probe.py`). Решения в очереди прогона у них нет, и понизить их некому — замер
только пишет вердикт замера. Так же — взятый из резерва и принятый (`review/reserve.py`):
вердикт прогона у него «не подходит», и человек принял его, это зная.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.rejudge import Stored, rejudge
from backend.features.review.queue import Decision
from backend.features.runs.verdicts import Judging, judging_of

#: Вердикт донора для выходов и его причина.
Standing = tuple[DonorStatus, str | None]


async def held_statuses(
    session: AsyncSession, results: Sequence[DomainResult], ids: Mapping[str, int]
) -> dict[int, Standing]:
    """Принятым донорам пачки — вердикт для выходов: номер домена → вердикт и причина.

    Строки принятых доноров берутся под замок до записи пачки: решение человека, снятое
    между чтением и записью, не должно лечь вердиктом поверх."""
    domain_ids = sorted({ids[result.host] for result in results if result.host in ids})
    if not domain_ids:
        return {}
    rows = await session.execute(
        select(DonorModel.domain_id, DonorModel.status, DonorModel.reject_reason)
        .where(DonorModel.domain_id.in_(domain_ids), DonorModel.review == Decision.ACCEPTED.value)
        .order_by(DonorModel.domain_id)
        .with_for_update()
    )
    current: dict[int, Standing] = {
        domain_id: (status, reason) for domain_id, status, reason in rows.tuples().all()
    }
    if not current:
        return {}
    accepted_in = await _accepted_in(session, list(current))
    judging = await _judgings(session, set(accepted_in.values()))
    held: dict[int, Standing] = {}
    for result in results:
        domain_id = ids.get(result.host)
        if domain_id is None or domain_id not in current:
            continue
        run_id = accepted_in.get(domain_id)
        held[domain_id] = (
            current[domain_id]
            if run_id is None
            else _judged(result, judging[run_id], current[domain_id])
        )
    return held


def _judged(result: DomainResult, judging: Judging, current: Standing) -> Standing:
    """Новые данные — вердиктом прогона, где приняли; судить нечем — вердикт прежний."""
    verdict = rejudge(Stored.of_result(result), judging.thresholds, judging.country, judging.rule)
    if verdict.status is DonorStatus.UNCHECKED:
        return current
    return verdict.status, verdict.reason


async def _accepted_in(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, int]:
    """Прогон, где донора приняли: строка решения, а не копия, — самая поздняя, как у
    решения донора (`RunReview._settle_donor`). Нет такой — принят без прогона; взят из
    резерва — тоже без прогона: годен словом человека, а не вердиктом прогона."""
    rows = await session.execute(
        select(RunCandidateModel.domain_id, RunCandidateModel.run_id, RunCandidateModel.taken_by)
        .where(RunCandidateModel.domain_id.in_(list(domain_ids)))
        .where(RunCandidateModel.status == Decision.ACCEPTED.value)
        .where(RunCandidateModel.carried.is_(False))
        .order_by(RunCandidateModel.domain_id, RunCandidateModel.decided_at.desc().nullslast())
        .distinct(RunCandidateModel.domain_id)
    )
    return {domain_id: run_id for domain_id, run_id, taken in rows.tuples().all() if taken is None}


async def _judgings(session: AsyncSession, run_ids: set[int]) -> dict[int, Judging]:
    """Чем судит каждый прогон приёма: его страна и версия настроек."""
    judging: dict[int, Judging] = {}
    for run_id in sorted(run_ids):
        judging[run_id] = await judging_of(session, await session.get_one(RunModel, run_id))
    return judging
