"""Тормоза конвейера «Плана работ»: когда прогон плана ставить нельзя — словами.

Один воркер долгих задач продаж (`sales-bulk`) делят письма и сбор, а дневной потолок модели продаж —
судья, письма, разбор ответов и агент («План работ», п. 3.3). Прогон плана — минуты воркера и
тысячи токенов, поэтому конвейер ставит его или продолжает остановленный, только когда ни один
тормоз не держит. Тормоз здесь — чтение без записи: держит — слова, почему; нет — `None`.

- **Повтор после сбоя** (`retry_after`) — у гипотезы проблема «прогон не пошёл»
  (`plan_budget.Problem.RUN_FAILED`), пока её гипотеза собирает: следующая попытка не раньше
  `SALES_PLAN_RETRY_MIN` от её времени. Остановленный прогон кладёт сюда время своей остановки.
- **Пауза между прогонами** (`paused`) — `SALES_PLAN_PAUSE_MIN` от конца последнего прогона плана на
  весь сервис.
- **Письма первыми** (`letters_first`) — в очереди стоит или идёт сборка очереди продаж (кнопкой или
  автосборкой, `queue_start.build_job_id`) или пачка «Отправить N» (`SEND_JOB_ID`); или включённой
  автосборке нужна сборка на ближайший день отправки и есть кого собрать (`autobuild.plan`). Нечего
  собрать — недостачу писем и закрывает сбор: ждать её значило бы не собирать никогда. Очередь не
  ответила — тоже держит: неизвестность — не «свободно».
- **Доля потолка модели** (`model_share`) — судья сегодня и смета прогона — не больше
  `SALES_PLAN_CAP_SHARE` дневного потолка модели продаж: меньшего из общего и своего у продаж, как у
  остатка сметы (`segment_estimate.tokens_left`); потолка нет — доли нет. Сегодняшний расход судьи —
  журнал расхода по операции судьи (`segment_judge.OPERATION`) с полуночи UTC, как считает дневной
  потолок: строки расхода прогона не знают, и в долю идут и прогоны «Сбора базы» — конвейер уступает
  им, как письмам. Доля выбрана целиком — держит и без сметы: смета зовёт модель проверкой.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, time, timedelta

from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.config import sales_plan as plan_cfg
from backend.features.core import llm_caps, usage
from backend.features.core.domain import Stage
from backend.features.sales import autobuild
from backend.features.sales.job_holder import holder
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_budget import Problem
from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.queue_start import RUNNING, build_job_id
from backend.features.sales.segment_judge import OPERATION as JUDGE
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_spend import number
from backend.features.sales.usage_cap import sales_cap

logger = logging.getLogger(__name__)

Run = SalesSegmentRunModel
#: Номер пачки «Отправить N» продаж — как её ставит экран рассылки (`api/letters/once.send_job_id`
#: этапа продаж; аудитория у продаж одна — «по ссылке»). Слой выше отсюда не импортируется —
#: равенство держит тест.
SEND_JOB_ID = f"letters-send-{Stage.SALES.value}-links"
QUEUE_SILENT = "очередь задач не ответила — идут ли сборка и письма, сейчас не узнать"
LETTERS_GOING = "идёт пачка писем продаж — прогон плана после неё"
BUILD_GOING = "идёт сборка очереди продаж — прогон плана после неё"


def _moscow(moment: datetime) -> str:
    return f"{moment.astimezone(llm_caps.MOSCOW):%d.%m %H:%M} по Москве"


def _when(raw: object) -> datetime | None:
    """Время проблемы гипотезы — строкой ISO (`plan_budget.problem`); не время — `None`."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("конвейер плана: время проблемы не прочитано", extra={"at": raw})
        return None


def retry_after(row: SalesPlanHypothesisModel, now: datetime) -> str | None:
    """Следующая попытка после сбоя не раньше `SALES_PLAN_RETRY_MIN` от времени проблемы."""
    failed = next((item for item in row.problems if item.get("code") == Problem.RUN_FAILED), None)
    at = _when(failed.get("at")) if failed is not None else None
    if at is None:
        return None
    until = at + timedelta(minutes=plan_cfg.RETRY_MIN)
    return None if now >= until else f"повтор после сбоя — не раньше {_moscow(until)}"


async def paused(session: AsyncSession, now: datetime) -> str | None:
    """Пауза `SALES_PLAN_PAUSE_MIN` от конца последнего прогона плана на весь сервис."""
    last = await session.scalar(
        select(func.max(Run.finished_at)).where(Run.plan_hypothesis_id.is_not(None))
    )
    if last is None:
        return None
    until = last + timedelta(minutes=plan_cfg.PAUSE_MIN)
    return None if now >= until else f"пауза между прогонами плана — до {_moscow(until)}"


def _going(jobs: Queue, job_ids: Iterable[str]) -> bool:
    """Стоит ли в очереди или идёт хоть одна из задач. Клиент Redis синхронный — зовут в потоке."""
    for job_id in job_ids:
        found = holder(jobs, job_id)
        if found is not None and found.get_status() in RUNNING:
            return True
    return False


def _busy(jobs: Queue, hypotheses: list[int]) -> str | None:
    """Чем занят воркер писем продаж: пачкой или сборкой; `None` — ничем."""
    if _going(jobs, [SEND_JOB_ID]):
        return LETTERS_GOING
    return BUILD_GOING if _going(jobs, map(build_job_id, hypotheses)) else None


async def letters_first(session: AsyncSession, jobs: Queue, now: datetime) -> str | None:
    """Письма первыми: сборка или пачка в очереди — или сборка, которая нужна автосборке."""
    hypotheses = list((await session.scalars(select(SalesHypothesisModel.id))).all())
    try:
        busy = await asyncio.to_thread(_busy, jobs, hypotheses)
    except RedisError as exc:
        logger.warning("конвейер плана: очередь задач не ответила", extra={"error": str(exc)})
        return QUEUE_SILENT
    if busy is not None or not sales_cfg.AUTOBUILD:
        return busy
    wanted = await autobuild.plan(session, jobs, now=now)
    if wanted.hypothesis_id is None:
        return None
    return (
        f"автосборке нужна сборка гипотезы «{wanted.name}» на {wanted.day or now.date():%d.%m}: "
        f"писем в очереди {number(wanted.have)} из {number(wanted.need)} — прогон плана после неё"
    )


def _daily() -> int:
    """Дневной потолок модели продаж, токены: меньший из общего и своего; 0 — потолка нет."""
    caps = [cap for cap in (llm_caps.daily_cap(), sales_cap().tokens) if cap]
    return min(caps) if caps else 0


async def model_share(session: AsyncSession, tokens: int, now: datetime) -> str | None:
    """Доля конвейера в дневном потолке модели продаж: судья сегодня и `tokens` сметы прогона."""
    await llm_caps.refresh(session)
    daily = _daily()
    if not daily:
        return None
    share = int(daily * plan_cfg.CAP_SHARE)
    since = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    spent = await usage.llm_tokens_spent(session, since=since, operations={JUDGE})
    if spent < share and spent + tokens <= share:
        return None
    return (
        f"доля конвейера в дневном потолке модели продаж — {number(share)} токенов: судья сегодня "
        f"потратил {number(spent)}, смета прогона ≈ {number(tokens)} — сбор продолжится после "
        f"сброса потолка {llm_caps.reset_words(now)}"
    )


class Gates:
    """Общие тормоза на проход — один ответ на все гипотезы: пауза, письма, доля потолка без сметы.
    Спрашиваются лениво, дешёвые первыми, и только когда есть что ставить."""

    def __init__(self, session: AsyncSession, jobs: Queue, now: datetime) -> None:
        self.session, self.jobs, self.now = session, jobs, now
        self._said: list[str | None] = []

    async def held(self) -> str | None:
        if not self._said:
            self._said.append(
                await paused(self.session, self.now)
                or await letters_first(self.session, self.jobs, self.now)
                or await model_share(self.session, 0, self.now)
            )
        return self._said[0]
