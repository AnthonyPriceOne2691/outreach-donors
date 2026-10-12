"""Прогон гипотезы: ключи → выдача → судья сегмента → адрес лестницей → лиды → очистка.

Этапы идут по порядку и каждый коммитит своё — прогон можно продолжить с места, где он
встал: упавшая задача повторяется очередью, остановленный потолком модели — продолжается
кнопкой, решения человека по «на проверку» — тем же продолжением.

1. **Выдача** (`segment_serp.collect`) — один раз: этап отмечен, повтор её не покупает.
2. **Судья** (`segment_judging.judge_run`) — кандидаты, ждущие судью; потолок модели
   выбран — прогон останавливается словами, неосуждённые ждут продолжения.
3. **Адрес** — общий вход лестницы `search_contacts(queue=…)` со своей очередью
   кандидатов (`segment_queue.py`): принятым без живого адреса на домене.
4. **Лиды и очистка** (`segment_leads.make_leads`) — принятым с адресом; очистка — та же,
   что у загрузки базы.

Числа этапов копятся в отчёте прогона (`report`): расход каждого прохода — свой, а
сколько сайтов в какой полосе, отчёт читает из кандидатов (`segment_report.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.contacts.search import search_contacts
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import NO_SEGMENT
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_judging import JudgeTally, judge_run
from backend.features.sales.segment_leads import make_leads
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.segment_serp import collect
from backend.features.sales.verifier import EmailVerifier
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

#: Числа судьи, которые копятся от прохода к проходу: каждый судил своих.
_SUMMED = ("judged", "from_cache", "by_rule", "would_cut", "tokens")


class UnknownRunError(LookupError):
    """Прогона нет: удалён или номер чужой. Повтор задачи это не исправит."""

    permanent = True


@dataclass(frozen=True, slots=True)
class RunDeps:
    """Исполнители прогона: выдача, модель, главные сайтов и проверка адресов."""

    serp: SerpProvider
    #: Клиент модели судьи; пусто — судья выключен.
    judge: SegmentClient | None
    #: Клиент для главных сайтов; пусто — судья судит по выдаче.
    home: httpx.AsyncClient | None
    verifier: EmailVerifier


def _judge_report(before: dict[str, Any] | None, tally: JudgeTally) -> dict[str, Any]:
    """Итог судьи за все проходы: расход и счёт — суммой, остановка и сбой — последнего."""
    now = tally.as_dict()
    earlier = before or {}
    return {**now, **{key: int(earlier.get(key, 0)) + int(now[key]) for key in _SUMMED}}


async def _contacts(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, Any]:
    """Поиск адреса принятым — общим входом лестницы, своей очередью кандидатов.

    Платная ступень — только когда она включена у сбора (`SALES_SEGMENT_PAID_CONTACTS`) и
    была в смете, которую человек подтвердил запуском: ключ Hunter общий с донорами, и его
    включение для доноров не должно молча включать платное сбору."""
    queue = SegmentContactQueue(session, run.id)
    waiting = await queue.pending_count()
    if waiting == 0:
        return {"pending": 0, "walked": 0, "saved": 0}
    paid = sales_cfg.SEGMENT_PAID_CONTACTS and int(run.estimate.get("hunter_searches", 0)) > 0
    found = await search_contacts(session, limit=waiting, queue=queue, no_paid=not paid)
    return found.as_dict()


async def execute(
    session: AsyncSession, run_id: int, deps: RunDeps, *, now: datetime | None = None
) -> dict[str, Any]:
    """Провести прогон с места, где он стоит. Возвращает отчёт прогона."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise UnknownRunError(f"прогона сбора №{run_id} нет")
    hypothesis = await session.get(SalesHypothesisModel, run.hypothesis_id)
    segment = (hypothesis.description or "").strip() if hypothesis is not None else ""
    run.error = None
    if run.stage == Stage.QUEUED:
        await collect(session, run, deps.serp)
        await session.commit()
    if not segment and JudgeMode(run.mode) is not JudgeMode.OFF:
        # Описание стёрли после запуска: судить не с чем, а угадывать сегмент модель не должна.
        run.stage, run.error = Stage.STOPPED, f"прогон остановлен: {NO_SEGMENT}"
        await session.commit()
        return dict(run.report)
    tally = await judge_run(
        session,
        run_id=run.id,
        segment=segment,
        mode=JudgeMode(run.mode),
        client=deps.judge,
        home=deps.home,
        now=now,
    )
    run.report = {**run.report, "judge": _judge_report(run.report.get("judge"), tally)}
    if tally.stopped is not None:
        run.stage, run.error = Stage.STOPPED, f"судья остановлен: {tally.stopped}"
        await session.commit()
        logger.warning("сбор продаж: прогон остановлен", extra={"run": run.id})
        return dict(run.report)
    run.stage = Stage.JUDGE
    await session.commit()
    run.report = {**run.report, "contacts": await _contacts(session, run)}
    run.stage = Stage.CONTACTS
    await session.commit()
    run.report = {**run.report, "leads": await make_leads(session, run, deps.verifier)}
    run.stage, run.finished_at = Stage.DONE, now or datetime.now(UTC)
    await session.commit()
    logger.info("сбор продаж: прогон прошёл", extra={"run": run.id})
    return dict(run.report)
