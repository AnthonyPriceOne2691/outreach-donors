"""Чья задача держит номер — в любой очереди: номер задачи един на весь Redis.

`Queue.fetch_job` отдаёт только задачи своей очереди (rq 2.12), а `unique=True` смотрит ключ
задачи на весь Redis. Долгие задачи продаж — сборка, автосборка, очистка, сбор базы — с 13.10.2026
ставятся в свою очередь (`sales-bulk`, процесс `worker-sales-bulk`), а до выкатки стояли в очереди
ответов продаж (`sales`). Своей новой очередью такая задача не нашлась бы: идущая сборка не
держала бы вторую (кнопка поставила бы её поверх — модель дважды), а кончившаяся держала бы номер
неделю («уже идёт»). Поэтому — своя очередь, а иначе `Job.fetch` по номеру, как у писем
(`api/letters/once._previous`).

**Место общему правилу — `shared/queue.py`**: его импортируют и `api/`, и `features/`, а модуль
продаж из `api/` не импортирует. До общего помощника здесь — то же правило для очередей продаж.
"""

from __future__ import annotations

import logging

from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

logger = logging.getLogger(__name__)


def holder(jobs: Queue, job_id: str) -> Job | None:
    """Задача, которая держит номер `job_id`: своей очереди — или любой другой. `None` — задачи
    нет. Очередь не ответила — исключение Redis вызывающему, как у `fetch_job`."""
    found = jobs.fetch_job(job_id)
    if found is not None:
        return found
    try:
        return Job.fetch(job_id, connection=jobs.connection, serializer=jobs.serializer)
    except NoSuchJobError:
        # Обычный ответ, а не сбой: у гипотезы ещё не было задачи или её след истёк. Проход
        # автосборки спрашивает каждые десять минут о каждой гипотезе — строка только отладочная.
        logger.debug(
            "продажи: задачи с этим номером нет ни в одной очереди", extra={"job_id": job_id}
        )
        return None
