"""Принятый человеком в прогоне — годен по вердикту этого прогона.

Адреса, письма, обход и перезапрос цены смотрят на вердикт донора (`donors.status`),
а он — последнего замера, той страны, что мерила. С вердиктом у прогона (аудит
20.10.2026) домен, отсеянный замером прогона Канады, предлагается в прогоне
Великобритании, где он годен; принятый там, он остался бы «не подходит», и ни адреса,
ни письма ему не было бы. Поэтому приём ставит донору вердикт прогона, где приняли:
годный там — годен. Дальше этот вердикт держит замер (`donors/held.py`): принятого
понижает только вердикт прогона, где его приняли.

Приём вердикта не ухудшает: прогон, который судить нечем (метрик донора нет —
заведён руками, липовый), донору свой вердикт не навязывает.

**Снятое решение возвращает вердикт последнего замера** (`settle_returned`, ревью
20.10.2026): приём поднял вердикт по прогону, где приняли, и без решения подъёму не на чем
стоять. Вернули в «предложен» или отклонили — донору снова вердикт его последнего замера
(`donors.measured_status`), какой стоял бы, не прими его человек. Замера не было (заведён
руками) — вердикт не трогается: возвращать нечего.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.review.queue import Decision
from backend.features.runs.verdicts import RunVerdicts


async def settle_decision(
    session: AsyncSession, run_id: int, domain_ids: Sequence[int], *, accepted: bool
) -> int:
    """Вердикт донора после решения в очереди прогона: принятым — вердикт прогона
    (`settle_accepted`), снятым и отклонённым — вердикт замера (`settle_returned`)."""
    if accepted:
        return await settle_accepted(session, run_id, domain_ids)
    return await settle_returned(session, domain_ids)


async def settle_accepted(session: AsyncSession, run_id: int, domain_ids: Sequence[int]) -> int:
    """Принятым в этом прогоне — вердикт прогона, если он «подходит». Возвращает, скольким
    донорам вердикт сменился. Вердикт прогона только спрашивается, не записывается:
    у прогонов до 20.10.2026 строк вердикта нет, и решение их не дописывает."""
    if not domain_ids:
        return 0
    run = await session.get_one(RunModel, run_id)
    rows = await session.execute(
        select(DomainModel.host, DomainModel.id).where(DomainModel.id.in_(list(domain_ids)))
    )
    ids = dict(rows.tuples().all())
    verdicts = await RunVerdicts(session).judged(run, list(ids), keep=False)
    suitable = sorted(ids[host] for host, verdict in verdicts.items() if verdict.suitable)
    if not suitable:
        return 0
    changed = await session.execute(
        update(DonorModel)
        .where(DonorModel.domain_id.in_(suitable))
        .where(DonorModel.status != DonorStatus.SUITABLE)
        .values(status=DonorStatus.SUITABLE, reject_reason=None)
        .returning(DonorModel.id)
    )
    return len(changed.all())


async def settle_returned(session: AsyncSession, domain_ids: Sequence[int]) -> int:
    """Донорам, которых больше никто не принял, — снова вердикт последнего замера.
    Возвращает, скольким вердикт вернулся."""
    if not domain_ids:
        return 0
    rows = await session.execute(
        select(DonorModel)
        .where(DonorModel.domain_id.in_(list(domain_ids)))
        .where(DonorModel.review.is_distinct_from(Decision.ACCEPTED.value))
        .where(DonorModel.measured_status.in_(_STATUSES))
        .execution_options(populate_existing=True)
    )
    returned = 0
    for donor in rows.scalars().all():
        # Отбор выше берёт только строки с вердиктом замера: пустого здесь нет.
        status = DonorStatus(cast(str, donor.measured_status))
        reason = None if status is DonorStatus.SUITABLE else donor.measured_reason
        if (donor.status, donor.reject_reason) == (status, reason):
            continue
        donor.status, donor.reject_reason = status, reason
        returned += 1
    await session.flush()
    return returned


#: Вердикты замера, которые бывают у донора.
_STATUSES = tuple(status.value for status in DonorStatus)
