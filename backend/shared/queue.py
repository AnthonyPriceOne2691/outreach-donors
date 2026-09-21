"""Очередь прогонов: имя, соединение и постановка задачи.

Зачем очередь вообще. Прогон идёт минутами: выдача, метрики, сохранение
пачками. Делать это внутри запроса значит держать соединение открытым
всё это время и потерять прогон, если человек закрыл вкладку, — а он
уже оплачен юнитами.

**Имя очереди одно и задаётся здесь.** Разъехавшиеся имена — это не
ошибка, а тишина: сервер кладёт задачу в `runs`, воркер слушает
`default`, и задача просто никогда не выполняется.

**Задача передаётся путём к функции, а не самой функцией.** Так воркеру
не нужно, чтобы вызывающий и он видели один и тот же объект в памяти;
это заодно проверяет, что функция импортируется в его окружении, —
а не выясняется при первом запуске в проде.
"""

from __future__ import annotations

import logging

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job

from backend.config import storage

logger = logging.getLogger(__name__)

#: Одно имя на весь проект. Менять — вместе с воркером.
QUEUE_NAME = "runs"

#: Пути задач строкой в одном месте. Раньше каждый маршрут держал свой:
#: пока путь знает только тот, кто ставит задачу, его некому свериться
#: с тем, кто ставит её заново — а разбор мёртвых прогонов ставит ту же
#: задачу, что и нажатие.
RUN_JOB = "backend.workers.jobs.run_donor_search"
BUILD_JOB = "backend.workers.jobs.build_letter_queue"
PARSE_JOB = "backend.workers.jobs.parse_reply"

#: Прогон идёт минутами и может упереться в ожидание провайдера.
#: Час — потолок, после которого задача считается зависшей: без него
#: умолчание в три минуты убивало бы каждый настоящий прогон.
JOB_TIMEOUT = 3600


def connection() -> Redis:
    """Соединение с очередью. Адрес — из конфига, как и всё остальное."""
    return Redis.from_url(storage.REDIS_URL)


def runs_queue(redis: Redis | None = None) -> Queue:
    return Queue(QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT)


#: Ключ живого воркера в Redis. Он держится heartbeat'ом самого rq
#: и исчезает вместе с процессом — это единственный признак, которому
#: можно верить.
WORKER_KEY_PREFIX = "rq:worker:"

#: Состояния, в которых задача считается живой сама по себе.
_LIVE_STATES = frozenset({"queued", "deferred", "scheduled"})


def workers_alive(redis: Redis | None = None) -> int | None:
    """Сколько живых воркеров слушает нашу очередь. `None` — не спросили.

    Нужно ровно для одного ответа человеку: «задача поставлена, и её
    некому взять». Без этого числа очередь без воркера выглядит как
    работающий сервис: сервер отвечает «поставлено», экран показывает
    успех, и дальше не происходит ничего — никогда и без единой записи.
    Проверено 21.09.2026: задача разбора провисела так сутки.
    """
    try:
        connection = redis or Redis.from_url(storage.REDIS_URL)
        return len(Worker.all(queue=Queue(QUEUE_NAME, connection=connection)))
    except RedisError as exc:
        logger.warning("очередь: не удалось спросить, есть ли воркеры — %s", exc)
        return None


def job_alive(job_id: str | None, redis: Redis | None = None) -> bool | None:
    """Жива ли задача: `True` / `False` / `None` (выяснить не удалось).

    **`started` не значит «работает».** Когда воркер умирает — деплой,
    нехватка памяти, убитый процесс, — его задача остаётся в этом
    состоянии до своего таймаута, а он у прогона час. Живость определяет
    воркер: его ключ живёт heartbeat'ом и исчезает вместе с процессом.

    **`None` — это не «мертва».** Redis недоступен, и приняв неизвестность
    за смерть, мы поставим вторую задачу на тот же прогон: он платный,
    и заплатим дважды. Вызывающий обязан различать эти два ответа.
    """
    if not job_id:
        return None
    try:
        connection = redis or Redis.from_url(storage.REDIS_URL)
        try:
            job = Job.fetch(job_id, connection=connection)
        except NoSuchJobError:
            # Задачи в Redis нет: её убрал перезапуск очереди без
            # сохранения или срок хранения результата. Это штатный
            # ответ «мертва», но след нужен — по нему видно, почему
            # прогон вдруг продолжают.
            logger.info("очередь: задачи %s в Redis нет — считаю мёртвой", job_id)
            return False
        except ValueError:
            # Номер задачи испорчен: правкой в базе, обрезкой колонки,
            # чем угодно. Такой задачи в очереди нет — но молчать об этом
            # нельзя, иначе прогон просто «не продолжается».
            logger.warning("очередь: негодный номер задачи %r — считаю, что её нет", job_id)
            return False
        state = job.get_status(refresh=True)
        if state != "started":
            return state in _LIVE_STATES
        return _worker_lives(job, connection)
    except RedisError as exc:
        logger.warning("очередь: не удалось узнать судьбу задачи %s — %s", job_id, exc)
        return None


def _worker_lives(job: Job, connection: Redis) -> bool:
    """Жив ли воркер, взявший задачу. Его ключ держится собственным
    heartbeat rq и исчезает вместе с процессом — в отличие от состояния
    задачи, которое так и остаётся «выполняется»."""
    owner = job.worker_name
    if not owner:
        return False
    return bool(connection.exists(f"{WORKER_KEY_PREFIX}{owner}"))
