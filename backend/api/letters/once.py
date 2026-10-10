"""Сборка и пачка писем — одна задача на этап и аудиторию за раз.

Проверка QA 10.10.2026: двойное «Собрать 50» ставило две сборки — вторая писала
следующие 50 писем и второй раз тратила модель; двойное «Отправить N» ставило две
пачки — вторая брала письма сверх тех N, что человек подтвердил. Теперь номер задачи
постоянный — этап и аудитория, — и пока задача с ним стоит в очереди, идёт или ждёт
повтора, вторая не ставится: маршрут отвечает 409 словами.

**Постоянный номер переживает свою задачу.** Готовая задача лежит в Redis весь
`result_ttl` (неделю), упавшая — год, и `unique=True` отвечает «уже есть» и на неё
(проверено на rq 2.12): одним номером новая сборка неделю получала бы 409. Поэтому
кончившаяся задача убирается и ставится заново, как сборка продаж
(`api/sales/queue.py`), а `unique=True` остаётся на гонку двух нажатий.

**Убирается и итог прежней.** rq хранит его отдельно от задачи, и `Job.delete()` его
не трогает: новая задача под тем же номером ещё в очереди, а её последним итогом
числился бы отчёт прежней (проверено на rq 2.12).
"""

from __future__ import annotations

from rq import Queue
from rq.exceptions import DuplicateJobError
from rq.job import Job, JobStatus
from rq.results import Result

from backend.features.core.domain import Stage

#: Задача ещё не кончилась: стоит в очереди, идёт, ждёт зависимости или повтора после сбоя.
RUNNING = frozenset({JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED})

BUILD_RUNNING = (
    "Сборка этой очереди уже идёт — дождитесь её итога под формой: вторая собрала бы "
    "письма сверх тех, что вы просили, и второй раз потратила модель"
)
SEND_RUNNING = (
    "Пачка этой очереди уже отправляется — дождитесь её итога у кнопки: вторая взяла бы "
    "письма сверх тех, что вы подтвердили"
)


def build_job_id(stage: Stage, audience: str) -> str:
    """Номер сборки — от этапа и аудитории: у вкладок свои очереди, и сборки свои."""
    return f"letters-build-{stage.value}-{audience}"


def send_job_id(stage: Stage, audience: str) -> str:
    """Номер пачки — от этапа и аудитории: пачка вкладки берёт только её письма."""
    return f"letters-send-{stage.value}-{audience}"


def enqueue_once(
    jobs: Queue, job_id: str, path: str, /, *args: object, **options: object
) -> Job | None:
    """Поставить задачу `path` под номером `job_id`. `None` — задача с этим номером
    ещё не кончилась, и второй не будет.

    Сперва — сразу с `unique=True`: свободный номер ставится одной командой. Занят —
    смотрим, кем: кончившаяся задача убирается вместе с итогом, идущая — отказ.
    """
    try:
        return jobs.enqueue(path, *args, job_id=job_id, unique=True, **options)
    except DuplicateJobError:
        previous = jobs.fetch_job(job_id)
    if previous is not None:
        if previous.get_status() in RUNNING:
            return None
        Result.delete_all(previous)
        previous.delete()
    try:
        return jobs.enqueue(path, *args, job_id=job_id, unique=True, **options)
    except DuplicateJobError:
        # Второе нажатие успело поставить свою между уборкой и постановкой — она и идёт.
        return None
