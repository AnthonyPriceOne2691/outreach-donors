"""«Найдено» гипотезы плана работ: один счёт у прохода конвейера и у решений человека.

«Найдено» — уникальные сайты в полосе «принят» у прогонов гипотезы плана (`found_sites`). Полосу
здесь не решают, а читают: её кладут судья по режиму (`segment_judge.effective`) и человек. Число
хранится в строке гипотезы плана (`SalesPlanHypothesisModel.found`): экран и API плана не
пересчитывают сотни прогонов на каждом опросе. Пишут его двое:

- **проход конвейера** (`plan_tick`) — у идущего прогона, у гипотезы, чей ход, и у гипотез в
  письмах — вместе с расходом (`plan_settle.count`); у гипотез в конце идущего плана — только его;
- **решение человека, которое меняет полосу сайта в прогоне плана** (`recount`) — сразу, в той же
  транзакции: «Принять» и «Отказ» (`segment_report.decide`), «Отменить решение»
  (`segment_undo.undo`), пересуд прогона (`segment_rejudge`). Стенд 19.11: человек принял сайт и
  отменил решение, а экран и API плана показывали прежнее число до следующего прохода, у
  остановленной гипотезы — насовсем: проход её не считал. Заведение лида полосу не меняет и не
  пересчитывает.

**Строка гипотезы плана — под замком до конца транзакции (`FOR UPDATE`), счёт — после замка.** Два
решения по сайтам одной гипотезы плана считают по очереди, и второе видит первое: счёт внутри
одного `UPDATE` смотрел бы на базу до ожидания замка и затёр бы чужое решение своим старым числом.
Проход конвейера берёт строку `SKIP LOCKED` — занятую пропускает до следующего раза; держит её
проход — решение ждёт его коммита и считает после.

**Пересчёт трогает только «найдено»**: время счёта (`counted_at`) — взгляд прохода, по нему проход
узнаёт прогон, кончившийся после его последнего взгляда (`plan_tick._fresh`), и отдаёт его итог в
реестр ключей; расход и резерв считает проход.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)

logger = logging.getLogger(__name__)

Row = SalesPlanHypothesisModel
Run = SalesSegmentRunModel
Candidate = SalesSegmentCandidateModel


async def found_sites(session: AsyncSession, plan_hypothesis_id: int) -> int:
    """«Найдено» гипотезы плана — уникальные сайты в полосе «принят» у её прогонов, одним
    запросом (шапка модуля)."""
    found = await session.scalar(
        select(func.count(func.distinct(Candidate.domain_id)))
        .select_from(Candidate)
        .join(Run, Run.id == Candidate.run_id)
        .where(Run.plan_hypothesis_id == plan_hypothesis_id, Candidate.lane == Lane.ACCEPT)
    )
    return int(found or 0)


async def recount(session: AsyncSession, run_id: int) -> int | None:
    """«Найдено» гипотезы плана, чей прогон `run_id`, — заново, после решения человека по его сайту:
    строка под замком, потом счёт (шапка модуля). Прогон не плана — ничего (`None`). Запись — в
    сессии вызывающего, коммит — за ним."""
    row = await session.scalar(
        select(Row)
        .join(Run, Run.plan_hypothesis_id == Row.id)
        .where(Run.id == run_id)
        .with_for_update(of=Row)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    was, row.found = row.found, await found_sites(session, row.id)
    await session.flush()
    if row.found != was:
        logger.info(
            "план работ: «найдено» пересчитано по решению человека",
            extra={"plan_hypothesis_id": row.id, "run": run_id, "was": was, "found": row.found},
        )
    return row.found
