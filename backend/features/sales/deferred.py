"""Отложенные задачи продаж — на срок, планировщиком rq воркера долгих задач продаж.

Механизм тот же, что у повтора задачи после сбоя (`shared/queue.RETRY_INTERVALS`) и переноса
разбора ответа на завтра (`workers/sales_jobs.postpone`): `Queue.enqueue_at`, а срабатывает
задачу планировщик воркера (`workers/main.py`, `with_scheduler=True`). Своего планировщика у
продаж нет, и процесса для него тоже.

**Номер — от минуты срока** (`job_id`): одна минута — одна задача. Номер занят — задача на эту
минуту уже стоит (или кончилась: тогда её срок прошёл, и следующий поставит она сама по базе).
Срок не раньше минуты от «сейчас» (`plan`): задача, ставящая следующую, не попадёт в свой номер.

**Срок живёт в базе, а не в задаче.** Задачу, потерянную очередью, поставит снова любой
следующий проход по сроку из базы, а отчёт прогона называет срок и даёт «Повторить сейчас».
Поэтому очередь, которая не ответила, — не отказ прохода, который её звал: строка журнала.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from rq.results import Result

from backend.features.sales.job_holder import holder
from backend.shared.queue import sales_bulk_queue, with_retries

logger = logging.getLogger(__name__)

#: Ближе этого срок не ставится: задача, ставящая следующую, иначе попала бы в свой же номер.
SOONEST = timedelta(minutes=1)
#: Задача ещё не кончилась: стоит в очереди, идёт, ждёт зависимости или повтора после сбоя.
RUNNING = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})


def job_id(prefix: str, when: datetime) -> str:
    """Номер отложенной задачи — приставка и минута срока по UTC."""
    return f"{prefix}{when.astimezone(UTC):%Y%m%dT%H%M}"


def at(jobs: Queue, prefix: str, path: str, when: datetime, *args: object) -> str | None:
    """Поставить задачу `path(*args)` на `when` под номером минуты срока. `None` — номер занят:
    задача на эту минуту уже есть."""
    number = job_id(prefix, when)
    if holder(jobs, number) is not None:
        return None
    jobs.enqueue_at(when, path, *args, job_id=number, **with_retries())
    return number


def plan(prefix: str, path: str, when: datetime, *args: object, now: datetime) -> str | None:
    """`at` в очередь долгих задач продаж — не раньше минуты от `now`. Очередь не ответила —
    строка журнала и `None`: срок лежит в базе, поставит следующий проход."""
    when = max(when, now + SOONEST)
    try:
        return at(sales_bulk_queue(), prefix, path, when, *args)
    except RedisError as exc:
        logger.warning(
            "продажи: отложенная задача не поставлена — очередь не ответила; срок в базе, "
            "её поставит следующий проход",
            extra={"job": path, "when": when.isoformat(), "error": str(exc)},
        )
        return None


def once(jobs: Queue, number: str, path: str, *args: object) -> Job | None:
    """Поставить `path(*args)` сейчас под номером `number` — если задача с ним кончилась или её
    нет: кончившаяся убирается вместе с итогом, как у постановки с экрана
    (`api/letters/once.enqueue_once`). `None` — номер держит живая задача, второй не будет."""
    earlier = holder(jobs, number)
    if earlier is not None:
        if earlier.get_status() in RUNNING:
            return None
        Result.delete_all(earlier)
        earlier.delete()
    try:
        return jobs.enqueue(path, *args, job_id=number, unique=True, **with_retries())
    except DuplicateJobError:
        logger.info("продажи: номер задачи занят другой постановкой", extra={"job_id": number})
        return None
