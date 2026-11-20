"""Что проход конвейера «Плана работ» пишет сам, без постановки задач: итог прогона в реестр ключей,
числа гипотезы плана и остались ли у её лидов первые письма.

- **Итог прогона** (`settle`) — один раз на кончившийся прогон плана (новизну судит проход):
  купленное — в реестр ключей (`plan_waves.mark_bought`: последняя страница и прогон, только вперёд);
  **ключ, которого источник не отдал** (`report.serp.lost_keywords`: заплачено, а не пришло), в
  `SALES_PLAN_LOST_TRIES` прогонах той же волны гипотезы плана на том же рынке — отработан: в реестре
  он куплен этим прогоном, и следующая пачка его не берёт. Иначе волна покупала бы его по кругу, а
  когда в ней останутся только такие ключи — одними ими. Повторная отметка ничего не портит.
- **Числа гипотезы** (`count`) — найдено (`plan_found.found_sites`), израсходовано и резерв идущего
  прогона (`plan_budget.spending` — расход плана одним чтением) и когда посчитано. Экран не
  пересчитывает сотни отчётов на опросе, а проход по `counted_at` узнаёт прогон, кончившийся после
  его последнего взгляда: время прохода берётся до чтения, и прогон, кончившийся посреди него, —
  позже. У гипотезы в конце проход пишет те же числа без времени счёта (`numbers`); решение
  человека по сайту прогона плана пересчитывает «найдено» сразу и `counted_at` не трогает
  (`plan_found.recount`).
- **Первые письма** (`letters_left`) — гипотеза в `sending` ждёт, пока у её лидов есть первое письмо
  в очереди, которое уйдёт (ещё не ушло, лид «готов»), или лид «готов» без письма, которому сборка
  его напишет (`queue_plan.decisions` — то же решение, что у сборки и «Напишет» вкладки «Рассылка»).
  Ни того ни другого — первые письма ушли, гипотеза `done`. Лид, которого сборка ждёт с причиной
  (нет цепочки, стоп-лист, передан телемаркетологу), письма сам не получит — его решает человек, а
  гипотеза не стоит из-за него в `sending` навсегда.

Запись — в сессии вызывающего, коммит — за ним.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import Text, any_, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import queue_plan
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesThreadModel
from backend.features.sales.plan_budget import Spent
from backend.features.sales.plan_found import found_sites
from backend.features.sales.plan_models import SalesPlanHypothesisModel, SalesPlanKeywordModel
from backend.features.sales.plan_waves import LOST, listed_keys, mark_bought
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_serp import KEY as SERP
from backend.features.sales.segment_serp import bought

logger = logging.getLogger(__name__)

Run = SalesSegmentRunModel
Key = SalesPlanKeywordModel
#: Первое письмо ещё не ушло: стоит в очереди или отправляется сейчас.
NOT_GONE = (MessageStatus.QUEUED, MessageStatus.SENDING)


async def settle(session: AsyncSession, run: SalesSegmentRunModel, *, lost_tries: int) -> int:
    """Итог кончившегося прогона плана — в реестр ключей (шапка модуля). Возвращает, скольким ключам
    отмечено купленное."""
    return await mark_bought(session, run) + await give_up(session, run, tries=lost_tries)


async def give_up(session: AsyncSession, run: SalesSegmentRunModel, *, tries: int) -> int:
    """Ключи, которых источник не отдал в `tries` прогонах волны этого прогона на его рынке, —
    отработаны: куплены этим прогоном (шапка модуля). Возвращает, скольким ключам отмечено."""
    if run.plan_hypothesis_id is None or not bought(run):
        return 0
    spent = await _lost_again(session, run, tries)
    if not spent:
        return 0
    given = await session.scalars(
        update(Key)
        .where(
            Key.plan_hypothesis_id == run.plan_hypothesis_id,
            Key.market == run.country,
            Key.keyword_key == any_(literal(spent, ARRAY(Text))),
            Key.pages < run.depth_pages,
        )
        .values(pages=run.depth_pages, run_id=run.id)
        .returning(Key.keyword)
        .execution_options(synchronize_session="fetch")
    )
    keywords = sorted(given.all())
    if keywords:
        logger.warning(
            "конвейер плана: ключи без выдачи отработаны — источник не отдал их и после повторов",
            extra={"run": run.id, "plan_hypothesis_id": run.plan_hypothesis_id, "keys": keywords},
        )
    return len(keywords)


async def _lost_again(session: AsyncSession, run: SalesSegmentRunModel, tries: int) -> list[str]:
    """Ключи прогона, которых источник не отдал, — правилом выдачи — те, что пропали уже в `tries`
    прогонах той же волны гипотезы плана на том же рынке (этот — среди них)."""
    lost = listed_keys(run.report[SERP].get(LOST))
    if not lost:
        return []
    answers = await session.scalars(
        select(Run.report[SERP][LOST]).where(
            Run.plan_hypothesis_id == run.plan_hypothesis_id,
            Run.country == run.country,
            Run.from_page == run.from_page,
            Run.depth_pages == run.depth_pages,
        )
    )
    times = Counter(key for answer in answers.all() for key in listed_keys(answer) & lost)
    return sorted(key for key, count in times.items() if count >= tries)


async def count(
    session: AsyncSession, row: SalesPlanHypothesisModel, spent: Spent, now: datetime
) -> None:
    """Числа гипотезы плана на `now`: найдено, израсходовано и резерв (шапка модуля)."""
    await numbers(session, row, spent)
    row.counted_at = now


async def numbers(session: AsyncSession, row: SalesPlanHypothesisModel, spent: Spent) -> None:
    """Найдено, израсходовано и резерв — без времени счёта: у гипотезы в конце проход итог её
    прогона в реестр ключей не отдаёт, и `counted_at` остаётся последним взглядом на него."""
    row.found = await found_sites(session, row.id)
    row.spent_usd, row.reserved_usd = spent.spent, spent.reserved


async def letters_left(session: AsyncSession, hypothesis_id: int, *, now: datetime) -> bool:
    """Остались ли у лидов гипотезы первые письма, которые уйдут (шапка модуля)."""
    queued = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(
            MessageModel.step == FIRST_STEP,
            MessageModel.status.in_(NOT_GONE),
            SalesLeadModel.hypothesis_id == hypothesis_id,
            SalesLeadModel.status == LeadStatus.READY,
        )
    )
    if queued:
        return True
    found = await queue_plan.decisions(session, now=now, hypothesis_id=hypothesis_id)
    return found.writes.get(hypothesis_id, 0) > 0
