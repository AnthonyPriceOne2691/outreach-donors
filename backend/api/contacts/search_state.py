"""Состояние поиска адресов — одно на доноров и рекламодателей.

Собирается из трёх мест: сколько ждёт в базе, что говорит очередь и что
вернула последняя задача. Своей строки в базе у задачи нет (`shared/queue.py`).
Доноры и рекламодатели Этапа 2 отличаются только очередью «кому искать» и
ключом, под которым помнится номер задачи. Две копии сборки разъехались бы
на первой правке, и одна из строк «ждут адреса» начала бы врать.
"""

from __future__ import annotations

import logging

from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job

from backend.api.contacts.schemas import ContactsState
from backend.api.jobs.routes import JobCard
from backend.features.ops.job_outcome import job_outcome
from backend.shared.queue import job_alive, runs_queue

logger = logging.getLogger(__name__)


def search_state(pending: int, job_id: str | None, workers: int | None) -> ContactsState:
    """Сколько ждёт адреса, идёт ли поиск и чем кончился прошлый."""
    running = bool(job_id) and job_alive(job_id) is True
    outcome = job_outcome(job_id) if job_id else None
    return ContactsState(
        pending=pending,
        running=running,
        job_id=job_id,
        last=last_report(job_id) if job_id and not running else None,
        workers=workers,
        job=JobCard.of(outcome) if outcome is not None else None,
    )


def last_report(job_id: str) -> dict[str, object] | None:
    """Отчёт законченной задачи. Живёт в самой очереди и исчезает
    вместе с ней — это нормально: числа нужны сразу после прохода,
    а не через неделю."""
    try:
        job = Job.fetch(job_id, connection=runs_queue().connection)
    except (NoSuchJobError, RedisError) as exc:
        logger.info("контакты: отчёт задачи %s недоступен (%s)", job_id, exc)
        return None
    result = job.latest_result() if job.is_finished else None
    value = result.return_value if result is not None else None
    return value if isinstance(value, dict) else None
