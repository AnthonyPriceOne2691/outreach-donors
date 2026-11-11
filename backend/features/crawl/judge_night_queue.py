"""Разбор спорных для ночного шага судьи — та же задача очереди обходов и тот же номер, что у кнопки
«Переобойти спорные» (`api/advertisers/judge_pass.py`, `workers/judge_jobs.judge_disputed`).

**Один номер на кнопку и на ночь.** Разбор тратит модель, и два разбора разом судили бы одни пары
дважды. Поэтому ночной шаг не заводит своей задачи: нажал человек раньше — шаг ждёт его разбор и
берёт его итог; шаг поставил свой — кнопка скажет «разбор уже идёт».

**Номер переживает свою задачу**: кончившаяся лежит в очереди неделю, и `unique=True` отвечает «уже
есть» и на неё — она убирается вместе с итогом и ставится заново (тот же порядок, что у кнопки:
`api/letters/once.py`; оттуда не импортируется — слой выше). Воркер умер вместе с задачей — она
числится идущей до своего срока, а живость говорит его ключ (`queue.job_alive`).

**Очередь не ответила — это не исход**: шаг ждёт следующего прохода, а не ставит второй разбор.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.results import Result

from backend.features.ops.job_outcome import job_outcome
from backend.shared.queue import (
    JUDGE_DISPUTED_JOB,
    JUDGE_DISPUTED_JOB_ID,
    JUDGE_DISPUTED_TIMEOUT,
    RESULT_TTL,
    crawl_queue,
    job_alive,
)

logger = logging.getLogger(__name__)

#: Задача ещё не кончилась: стоит в очереди, идёт, ждёт зависимости или повтора.
GOING = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})
WORKER_DIED = "обходчик перезапустился посреди разбора"

State = Literal["idle", "going", "done", "failed", "unknown"]

#: Исход задачи (`ops/job_outcome.py`) → что это значит шагу, пока задача не кончилась.
_STATES: dict[str, State] = {
    "unknown": "unknown",
    "queued": "going",
    "retry_wait": "going",
    "running": "going",
}


@dataclass(frozen=True, slots=True)
class PassSeen:
    """Что очередь знает о разборе спорных. `idle` — задачи нет; `unknown` — очередь не ответила."""

    state: State
    #: Итог задачи как она его вернула (`judge_jobs._pass`) — у `done`.
    report: dict[str, Any] | None = None
    ended_at: datetime | None = None
    error: str | None = None


class PassQueue(Protocol):
    """Очередь разбора глазами ночного шага: тесты подставляют свою."""

    def seen(self) -> PassSeen: ...

    def start(self, cases: int, *, by: str) -> bool | None:
        """Поставить разбор. `True` — поставлен; `False` — один уже идёт, ждём его; `None` —
        очередь не ответила."""
        ...


def _aware(moment: datetime | None) -> datetime | None:
    """Время очереди — в UTC: старые версии rq отдают его без пояса."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


class RqPass:
    """Разбор спорных в настоящей очереди обходов."""

    def seen(self) -> PassSeen:
        outcome = job_outcome(JUDGE_DISPUTED_JOB_ID)
        if outcome is None:
            return PassSeen("idle")
        ended = _aware(outcome.ended_at)
        if outcome.state == "done" and outcome.report is not None:
            return PassSeen("done", outcome.report, ended)
        # «Идёт» у задачи умершего воркера держится до её срока: живость говорит его ключ.
        if outcome.state == "running" and job_alive(JUDGE_DISPUTED_JOB_ID) is False:
            return PassSeen("failed", error=WORKER_DIED)
        # Остальное — упала, отказана, кончилась без отчёта — «не дошла»: шаг поставит снова.
        return PassSeen(_STATES.get(outcome.state, "failed"), ended_at=ended, error=outcome.error)

    def start(self, cases: int, *, by: str) -> bool | None:
        try:
            return _start(crawl_queue(), cases, by)
        except RedisError as exc:
            logger.warning(
                "судья ночью: очередь не ответила, разбор не поставлен", extra={"error": str(exc)}
            )
            return None


def _enqueue(jobs: Queue, cases: int, by: str) -> Job | None:
    """Поставить разбор под постоянным номером; номер занят — `None`."""
    try:
        return jobs.enqueue(
            JUDGE_DISPUTED_JOB,
            cases,
            job_id=JUDGE_DISPUTED_JOB_ID,
            unique=True,
            at_front=True,
            job_timeout=JUDGE_DISPUTED_TIMEOUT,
            result_ttl=RESULT_TTL,
            meta={"by": by, "total": cases},
        )
    except DuplicateJobError:
        logger.info("судья ночью: номер разбора занят — смотрим, кончился ли прежний")
        return None


def _previous(jobs: Queue) -> Job | None:
    """Задача, которая держит номер; её уже нет — `None`."""
    found = jobs.fetch_job(JUDGE_DISPUTED_JOB_ID)
    if found is not None:
        return found
    try:
        return Job.fetch(
            JUDGE_DISPUTED_JOB_ID, connection=jobs.connection, serializer=jobs.serializer
        )
    except NoSuchJobError:
        logger.info("судья ночью: задачи с номером разбора уже нет — ставим")
        return None


def _start(jobs: Queue, cases: int, by: str) -> bool:
    """Поставить разбор; идёт другой (его поставила кнопка) — не ставить и ждать его."""
    if _enqueue(jobs, cases, by) is not None:
        return True
    previous = _previous(jobs)
    if previous is not None:
        # Задача умершего воркера числится идущей: живость говорит его ключ, а не статус.
        if previous.get_status() in GOING and job_alive(JUDGE_DISPUTED_JOB_ID) is not False:
            return False
        Result.delete_all(previous)
        previous.delete()
    return _enqueue(jobs, cases, by) is not None
