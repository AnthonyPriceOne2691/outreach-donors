"""Поставить сборку очереди продаж — кнопкой экрана и автосборкой, одну на гипотезу за раз.

Сборка зовёт модель на каждое письмо — минуты, поэтому она задача очереди продаж
(`queue_jobs.build_sales_queue`). Номер задачи — от гипотезы (`build_job_id`): вторая сборка
той же гипотезы, пока первая идёт или ждёт повтора, — отказ (`DuplicateJobError`): она собрала
бы те же письма и потратила модель дважды. Правило одно у кнопки (`api/sales/queue.py`) и у
автосборки (`autobuild.py`): до автосборки оно жило в маршруте, а процесс разбора маршрутов
не импортирует.

Сборку, поставленную автосборкой, помечает мета задачи (`AUTO`): экран очереди называет её итог
«собрано само», а сборку кнопкой — нет.
"""

from __future__ import annotations

from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from rq.results import Result

from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.shared.queue import with_retries

#: Писем за одну сборку: больше — модель часами, а очередь пачкой всё равно идёт днями.
LIMIT_MAX = 200
#: Сборка ещё идёт или ждёт повтора — вторую не ставим.
RUNNING = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})
BUILD_RUNNING = (
    "Сборка очереди этой гипотезы уже идёт — дождитесь её итога: вторая собрала бы те же письма "
    "и потратила модель дважды"
)
#: Ключ меты задачи: сборку поставила автосборка, а не человек.
AUTO = "auto"


def build_job_id(hypothesis_id: int) -> str:
    """Номер задачи сборки — от гипотезы: одна сборка гипотезы за раз."""
    return f"sales-queue-{hypothesis_id}"


def start(jobs: Queue, hypothesis_id: int, limit: int, *, auto: bool = False) -> Job:
    """Поставить сборку гипотезы, если её сборка не идёт; `auto` — ставит автосборка.

    Готовая задача лежит в Redis весь `result_ttl` (неделю), поэтому один `unique=True` с постоянным
    номером отказывал бы и после неё: закончившуюся или упавшую сборку убираем — вместе с итогом —
    и ставим заново. `unique=True` остаётся на гонку двух постановок между проверкой и постановкой.
    """
    job_id = build_job_id(hypothesis_id)
    previous = jobs.fetch_job(job_id)
    if previous is not None:
        if previous.get_status() in RUNNING:
            raise DuplicateJobError(job_id)
        # Итог прежней rq 2.12 хранит отдельно от задачи, и `Job.delete()` его не трогает:
        # новая сборка под тем же номером, пока стоит в очереди, показала бы на экране отчёт
        # прежней. Тот же вызов — у писем (`api/letters/once.enqueue_once`) и у очистки
        # (`clean._clean_once`); когда он переедет в `shared/queue.py`, сборка перейдёт на него.
        Result.delete_all(previous)
        previous.delete()
    return jobs.enqueue(
        QUEUE_JOB,
        hypothesis_id,
        limit,
        job_id=job_id,
        unique=True,
        meta={AUTO: auto},
        **with_retries(),
    )
