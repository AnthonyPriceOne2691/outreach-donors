"""Чем кончилась фоновая задача — словами человека.

Экран ставит задачу и получает её номер; дальше он спрашивает здесь, что
с ней. До этого модуля упавшая сборка писем выглядела как «письма просто
не появились», упавший поиск контактов — как тишина: отчёт показывался
только у удачной задачи, и человек гадал, идёт она, упала или ждёт.

**Исходов шесть, и путать их дорого:**

- «в очереди» — никто ещё не взял;
- «идёт»;
- «ждёт повтора» — упала на временном сбое, очередь попробует ещё раз
  в названное время (`queue.RETRY_INTERVALS`), причина видна уже сейчас;
- «готово» — с отчётом задачи;
- «не выполнена» — постоянный отказ (ключ, настройка, шаблон): повтор
  не исправит, задача вернула причину вместо отчёта (`workers/jobs._settled`);
- «упала» — повторы кончились, причина — строка последнего исключения.

Итог живёт в самой очереди: rq хранит упавшие задачи год, удачные —
неделю (`queue.RESULT_TTL`). Очередь не ответила — это отдельный исход,
а не «задачи нет»: иначе недоступный Redis выглядел бы как пропавшая задача.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.registry import ScheduledJobRegistry

from backend.features.runs.reasons import explained_line
from backend.shared.queue import (
    BUILD_JOB,
    CONTACTS_JOB,
    PARSE_JOB,
    QUEUE_NAME,
    RUN_JOB,
    connection,
    job_error,
    last_error_line,
)

logger = logging.getLogger(__name__)

#: Задача по пути — словами человека.
KINDS = {
    RUN_JOB: "прогон",
    BUILD_JOB: "сборка писем",
    CONTACTS_JOB: "поиск контактов",
    PARSE_JOB: "разбор ответа",
}

TITLES = {
    "queued": "в очереди",
    "running": "идёт",
    "retry_wait": "ждёт повтора",
    "done": "готово",
    "refused": "не выполнена",
    "failed": "упала",
    "unknown": "очередь не отвечает",
}


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """Исход задачи для экрана."""

    job_id: str
    kind: str
    state: str
    error: str | None = None
    report: dict[str, Any] | None = None
    retries_left: int | None = None
    next_try_at: datetime | None = None
    ended_at: datetime | None = None

    @property
    def title(self) -> str:
        return TITLES[self.state]


def job_outcome(job_id: str, redis: Redis | None = None) -> JobOutcome | None:
    """Исход задачи по номеру. `None` — такой задачи очередь не знает
    (срок хранения итога истёк или номер чужой)."""
    try:
        conn = redis or connection()
        job = Job.fetch(job_id, connection=conn)
        return _outcome(job, conn)
    except NoSuchJobError:
        logger.info("задачи %s в очереди нет — итог истёк или номер чужой", job_id)
        return None
    except RedisError as exc:
        logger.warning("очередь не ответила о задаче %s: %s", job_id, exc)
        return JobOutcome(job_id=job_id, kind="задача", state="unknown", error=str(exc))


#: Статус очереди → исход. «finished» уточняется отчётом: причина вместо
#: отчёта — это постоянный отказ, а не удача.
_STATES = {
    "finished": "done",
    "scheduled": "retry_wait",
    "started": "running",
    "failed": "failed",
    "stopped": "failed",
    "canceled": "failed",
}


def _outcome(job: Job, conn: Redis) -> JobOutcome:
    # ⚠ `.value`, а не `str()`: у перечисления rq `str()` даёт «JobStatus.FINISHED»,
    # и сравнение со строкой молча не срабатывало — все задачи выглядели
    # «в очереди» (поймано живой проверкой с настоящим воркером).
    raw = job.get_status(refresh=True)
    status = str(getattr(raw, "value", raw) or "")
    result = job.latest_result()
    value = result.return_value if result is not None else None
    report = value if isinstance(value, dict) else None
    state = _STATES.get(status, "queued")
    if state == "done" and report is not None and report.get("error"):
        state = "refused"
    return JobOutcome(
        job_id=job.id,
        kind=KINDS.get(job.func_name or "", job.func_name or "задача"),
        state=state,
        error=_error_of(state, report, result, job, conn),
        report=report,
        retries_left=job.retries_left,
        next_try_at=_next_try(job, conn) if state == "retry_wait" else None,
        ended_at=job.ended_at,
    )


def _error_of(
    state: str,
    report: dict[str, Any] | None,
    result: Any,
    job: Job,
    conn: Redis,
) -> str | None:
    """Причина — только у тех исходов, где она есть, и словами человека.

    Имя класса исключения на экран не выходит (правило `runs/reasons.py`):
    до 25.09.2026 строка задачи на экранах писем и контактов печатала
    «TemplateError: …», а без сохранённой причины — «задача failed», статус
    очереди по-английски. Сырой текст с классом остаётся в журнале и в самой
    очереди — по нему сбой и ищут.
    """
    if state == "refused" and report is not None:
        return explained_line(str(report["error"]))
    if state not in {"retry_wait", "failed"}:
        return None
    # Попытка, ушедшая на повтор, итога не оставляет: причину кладёт рядом
    # сама задача (`workers/jobs._remember_error`).
    found = last_error_line(result.exc_string) if result is not None else None
    raw = found or job_error(job.id, conn)
    return explained_line(raw) if raw else "причина не сохранилась"


def _next_try(job: Job, conn: Redis) -> datetime | None:
    try:
        registry = ScheduledJobRegistry(  # type: ignore[no-untyped-call]  # rq без аннотаций
            queue=Queue(QUEUE_NAME, connection=conn)
        )
        return registry.get_scheduled_time(job)
    except NoSuchJobError:
        logger.info("задача %s уже не отложена — время повтора неизвестно", job.id)
        return None
