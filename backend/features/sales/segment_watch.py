"""Жив ли прогон сбора — по очереди задач, а не по строке прогона.

Строка прогона говорит, где он был, когда писал в последний раз; жив ли он сейчас, знает
очередь (`shared/queue`): задача стоит в очереди, идёт у живого воркера или ждёт повтора.
До 14.10 умерший воркер и пропавшая задача оставляли прогон «идёт» навсегда, а задача,
которую некому взять, выглядела работающей (правило видимости отказов).

- **Идёт** (`going`): задача прогона жива — в очереди, у живого воркера, ждёт повтора; что
  именно — `job` (состояние задачи rq): стоящий в очереди прогон не «судит», хоть стадия у
  него и «судья» с прошлого прохода.
- **Остановлен** (`stalled`): строка «идёт», а задачи нет, она упала или умер её воркер —
  словами, и экран предлагает «Продолжить». Задачу умершего воркера rq держит «идёт» до
  таймаута (час), и продолжение отвечало бы «уже идёт» — её убирает `drop_dead`.
- **Некому взять** (`workers == 0`): воркеров очереди долгих задач продаж нет — экран
  говорит это словами, как у обходов (`api/crawls/routes.py`).
- **Очередь не ответила** (`silent`): «не знаю» — не «мертва» (`queue.job_alive`):
  остановленным такой прогон не называется, и задачу его никто не убирает.

Строку прогона чтение не правит: что видно сейчас — вывод из очереди, а запись остаётся
свидетельством. Клиент Redis синхронный — вызывающий зовёт отсюда в пуле потоков.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from rq.results import Result

from backend.features.runs.reasons import explained_line
from backend.features.sales.job_holder import holder
from backend.features.sales.segment_jobs import segment_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.shared.queue import (
    SALES_BULK_QUEUE_NAME,
    connection,
    job_alive,
    job_failure,
    workers_alive,
)

logger = logging.getLogger(__name__)

#: Этапы, в которых прогон «идёт» по своей строке.
GOING = frozenset({Stage.QUEUED, Stage.SERP, Stage.JUDGE, Stage.CONTACTS, Stage.LEADS})

#: Задачи нет в очереди — или её воркер умер, не дописав строку прогона.
LOST = (
    "задачи прогона в очереди нет: её воркер умер или очередь перезапускали — сделанное "
    "сохранено, продолжите прогон"
)
#: Задача упала, а строка прогона не записала почему (база не ответила и на это).
FELL = "задача прогона упала: {why} — сделанное сохранено, продолжите прогон"


@dataclass(frozen=True, slots=True)
class Watch:
    """Что с задачей прогона сейчас."""

    #: Задача прогона жива: стоит в очереди, идёт или ждёт повтора — экран опрашивает.
    going: bool
    #: Строка «идёт», а задачи нет: почему — словами; экран — «остановлен» и «Продолжить».
    stalled: str | None = None
    #: Живых воркеров очереди долгих задач продаж; `None` — очередь не ответила.
    workers: int | None = None
    #: Очередь не ответила о задаче: жив ли прогон, сейчас не узнать.
    silent: bool = False
    #: Состояние живой задачи rq: `queued`, `deferred`, `scheduled` (ждёт повтора), `started`;
    #: пусто — задача не жива или узнать не вышло.
    job: str | None = None


def unfinished(run: SalesSegmentRunModel) -> bool:
    """О ком спрашивать очередь: строка «идёт» — или проход поставлен продолжением и ещё не
    кончился (`finished_at` пуст, `resume_run`)."""
    return run.stage in GOING or run.finished_at is None


def job_state(job_id: str) -> str | None:
    """Состояние задачи строкой (`.value`, а не `str()`: у перечисления rq `str()` даёт
    «JobStatus.QUEUED», и сравнение молча не сработало бы). Задачи нет или очередь не
    ответила — пусто: о живости уже сказал `job_alive`, здесь — только уточнение."""
    try:
        raw = Job.fetch(job_id, connection=connection()).get_status(refresh=False)
    except (NoSuchJobError, RedisError) as exc:
        logger.info(
            "сбор продаж: состояние задачи не узнать", extra={"job_id": job_id, "why": str(exc)}
        )
        return None
    return str(getattr(raw, "value", raw) or "") or None


def watch(run_id: int, stage: str, *, workers: int | None) -> Watch:
    """Жив ли прогон: задача — по номеру прогона (`segment_job_id`), как её ставит экран."""
    job_id = segment_job_id(run_id)
    alive = job_alive(job_id)
    if alive is None:
        return Watch(going=stage in GOING, workers=workers, silent=True)
    if alive:
        return Watch(going=True, workers=workers, job=job_state(job_id))
    if stage not in GOING:
        # Готовый или остановленный прогон, продолжение которого не дошло до воркера:
        # говорить нечего — кнопки у него те же, что были.
        return Watch(going=False, workers=workers)
    failure = job_failure(job_id)
    why = FELL.format(why=explained_line(failure)) if failure else LOST
    logger.warning("сбор продаж: прогон «идёт», а задачи нет", extra={"run": run_id, "why": why})
    return Watch(going=False, stalled=why, workers=workers)


def watch_runs(asked: Sequence[tuple[int, str]]) -> dict[int, Watch]:
    """Очередь — о прогонах `(номер, стадия)`; о воркерах — один вопрос на всех."""
    if not asked:
        return {}
    workers = workers_alive(queue=SALES_BULK_QUEUE_NAME)
    return {run_id: watch(run_id, stage, workers=workers) for run_id, stage in asked}


def drop_dead(jobs: Queue, run_id: int) -> bool:
    """Убрать задачу прогона, чей воркер умер посреди прохода, — когда продолжению ответили
    «номер занят».

    rq держит такую задачу «идёт» до своего таймаута (час), и постановка продолжения
    (`api/letters/once.enqueue_once`) отвечала бы «прогон уже идёт» на прогон, который не
    идёт. Убирается только «идёт» с умершим воркером (`queue.job_alive` — `False`): живая,
    ждущая повтора и неизвестная (`None` — очередь не ответила) остаются как есть.
    `True` — убрали, и номер свободен."""
    job_id = segment_job_id(run_id)
    found = holder(jobs, job_id)
    if found is None or found.get_status(refresh=True) != JobStatus.STARTED:
        return False
    if job_alive(job_id, redis=jobs.connection) is not False:
        return False
    Result.delete_all(found)
    found.delete()
    logger.warning(
        "сбор продаж: задача прогона «идёт», а её воркер умер — убрана перед продолжением",
        extra={"run": run_id, "job_id": job_id},
    )
    return True
