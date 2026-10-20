"""Повтор поиска адреса сайтам прогона, которые не ответили, — сам, когда срок настал.

Разбор прогона сбора 20.10: сайт, не ответивший поиску, получал «сайт не ответил — повтор по
сроку» (общее правило `contacts/attempts.py`: следующий проход, через день, через неделю, затем
«адреса нет»), а проход адресов ставили только запуск и «Продолжить» — срок наступал, и никто не
искал. Теперь после каждого прохода прогона — и после сбоя тоже (`segment_jobs.run_once`) —
очередь получает задачу на самый ранний срок молчавших сайтов прогона (`plan`, `deferred.py`:
`enqueue_at` и планировщик воркера долгих задач продаж); срок — общего правила, но не раньше
`SALES_SEGMENT_AGAIN_MIN` минут после попытки (`segment_queue.retry_at`).

**Задача на срок только ставит проход** (`segment_jobs.due_again`): проход прогона идёт под
номером прогона (`segment_jobs.segment_job_id`), как «Продолжить», — один проход прогона за
раз, экран видит его идущим, а «Продолжить» в это время отвечает «уже идёт». Номер прогона
занят или идёт пересуд прогона — пропуск, и задача на срок — снова через
`SALES_SEGMENT_AGAIN_MIN` (`later`): идущий проход мог кончиться сбоем, не поставив своего.

**Что делает проход по сроку** (`segment_run.again`, `Again.DUE`): адрес — только сайтам «не
ответил», чей срок настал (`segment_queue.Retry`), и лиды — только из найденного этим проходом.
Ни выдачи, ни судьи, ни первого поиска принятым: первый поиск стоит денег, и запускает его
человек кнопкой «Найти адреса принятым». Платная ступень молчавшему сайту — по общему правилу: в
последний проход без ответа (`attempts.on_last_try`), только если она была в смете запуска, и не
больше её остатка (`segment_queue.paid_left`).

Срок — в базе: задачу, потерянную очередью, поставит следующий проход прогона, а отчёт называет
срок, «наступил» и даёт «Повторить сейчас» (`segment_waits.py`).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales import deferred
from backend.features.sales.segment_queue import SegmentContactQueue, least

#: Задача на срок — путём строкой: она ставит проход прогона под его номером.
TRIGGER = "backend.features.sales.segment_jobs.due_again"


def prefix(run_id: int) -> str:
    """Приставка номера задачи на срок у прогона; хвост — минута срока (`deferred.job_id`)."""
    return f"sales-segment-again-{run_id}-"


async def next_at(session: AsyncSession, run_id: int) -> datetime | None:
    """Самый ранний срок, когда прогон сам спросит молчавший сайт; таких нет — пусто."""
    return min(await SegmentContactQueue(session, run_id).silent_retries(), default=None)


async def plan(
    session: AsyncSession, run_id: int, *, now: datetime, soonest: timedelta = deferred.SOONEST
) -> str | None:
    """Поставить задачу на самый ранний срок молчавших сайтов прогона, не раньше `soonest` от
    `now`. Номер поставленной; `None` — молчавших нет, задача на эту минуту уже стоит или очередь
    не ответила."""
    when = await next_at(session, run_id)
    if when is None:
        return None
    return deferred.plan(prefix(run_id), TRIGGER, when, run_id, now=now, soonest=soonest)


def later(run_id: int, *, now: datetime) -> str | None:
    """Срок пропущен (проход прогона или пересуд идёт): задача на срок — снова, через
    `SALES_SEGMENT_AGAIN_MIN` минут. Идущий проход поставит свой срок и сам — это страховка на
    случай, когда он кончится сбоем."""
    return deferred.plan(prefix(run_id), TRIGGER, now + least(), run_id, now=now)
