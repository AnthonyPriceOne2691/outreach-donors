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

import hashlib
import logging
import uuid
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Retry, Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job

from backend.config import storage

logger = logging.getLogger(__name__)

#: Одно имя на весь проект. Менять — вместе с воркером.
QUEUE_NAME = "runs"

#: Очередь обходов Этапа 2 — своя, у своего сервиса (`workers/crawler.py`).
#: Обход донора идёт до получаса, и в общей очереди он занял бы единственный
#: воркер: разбор ответов и сборка писем ждали бы его.
CRAWL_QUEUE_NAME = "crawl"

#: Очередь ответов продаж — своя, у своего воркера (`worker-sales`): в общей
#: очереди ответ лида ждал бы часовой прогон доноров.
SALES_QUEUE_NAME = "sales"

#: Долгие задачи продаж — своя очередь у своего процесса (`worker-sales-bulk`, решение
#: Anthony 13.10.2026): пачка писем, сборка очереди и автосборка, сбор базы, очистка. В очереди
#: ответов они держали бы разбор ответа лида и черновик агента за собой — сбор базы идёт
#: до получаса. Вторая копия `worker-sales` не помогла бы: две долгие задачи заняли бы обе.
SALES_BULK_QUEUE_NAME = "sales-bulk"

#: Короткие задачи, которых ждёт человек, — своя очередь у своего процесса
#: (`worker-quick`): разбор ответа донора, черновик агента, передача лида, пересчёт
#: кандидатов после обхода. В общей очереди они стояли за прогоном доноров и поиском
#: адресов до часа: цена из ответа появлялась, когда прогон кончался (разбор 14.10.2026).
QUICK_QUEUE_NAME = "quick"

#: Поиск адресов — своя очередь у своего процесса (`worker-contacts`): поиск по тысяче
#: доменов рекламодателей держал бы прогоны и письма за собой. Процесс один: строки
#: очереди адресов ничем не заняты (`contacts/attempts.py`), и два потребителя прошли
#: бы одни и те же домены дважды — платной ступени тоже.
CONTACTS_QUEUE_NAME = "contacts"

#: Пути задач строкой в одном месте. Раньше каждый маршрут держал свой:
#: пока путь знает только тот, кто ставит задачу, его некому свериться
#: с тем, кто ставит её заново — а разбор мёртвых прогонов ставит ту же
#: задачу, что и нажатие.
RUN_JOB = "backend.workers.jobs.run_donor_search"
BUILD_JOB = "backend.workers.jobs.build_letter_queue"
SEND_QUEUE_JOB = "backend.workers.send_jobs.send_letter_queue"
PARSE_JOB = "backend.workers.jobs.parse_reply"
CONTACTS_JOB = "backend.workers.jobs.find_contacts"
#: «Искать глубже» и текст письма для формы — по кнопке, по донору (`workers/contact_jobs.py`).
DEEPER_JOB = "backend.workers.contact_jobs.find_deeper"
FORM_LETTER_JOB = "backend.workers.contact_jobs.build_form_letter"
LEAD_JOB = "backend.workers.jobs.send_lead"
CRAWL_JOB = "backend.workers.crawl_jobs.crawl_donor"
JUDGE_CRAWL_JOB = "backend.workers.crawl_jobs.judge_crawl"
SALES_REPLY_JOB = "backend.workers.sales_jobs.sales_reply"

#: Прогон идёт минутами и может упереться в ожидание провайдера.
#: Час — потолок, после которого задача считается зависшей: без него
#: умолчание в три минуты убивало бы каждый настоящий прогон.
JOB_TIMEOUT = 3600

#: Потолок задачи прогона доноров — свой, дольше общего часа. Суд пятисот доменов
#: на восьми одновременных шёл полчаса (разбор 14.10.2026), и прогон в семьсот с
#: лишним доменов в час не помещался: его убивали посреди суда, а продолжение
#: начинало суд заново. Вердикты теперь сохраняются пачками, и запас нужен, чтобы
#: большой прогон кончался сам, а не по таймауту очереди.
RUN_JOB_TIMEOUT = 3 * 3600

#: Запас задачи обхода сверх его срока: после проверки срока ещё идут страница с каскадом
#: уровней (до минуты), последняя пачка и запись итога.
CRAWL_JOB_MARGIN_SEC = 3600


def crawl_job_timeout() -> int:
    """Потолок задачи обхода: самый долгий срок обхода по настройкам — общий
    (`CRAWL_MAX_SECONDS`) или сайта с долгой паузой (`CRAWL_MAX_SECONDS_SLOW`, `crawl/pace.py`)
    — плюс запас. Таймаут очереди — последняя страховка от зависания, а не способ остановить
    обход: до 23.10.2026 здесь стояли вшитые два часа, и обход со сроком в три убивался бы
    посреди страницы, теряя пачку и попытку разбора мёртвых. Продолжение — новая задача с тем
    же потолком, а время обхода считается с прошлых попыток: срок донора не превысит.

    Конфиг обхода — здесь, а не при импорте: очередь импортируют все процессы, а опечатка
    в `CRAWL_*` роняет только обходчик (`tests/test_crawl_config_isolation.py`).
    """
    from backend.config import crawl  # noqa: PLC0415 — нужен только обходам, см. выше

    return int(max(crawl.MAX_SECONDS_PER_DONOR, crawl.MAX_SECONDS_SLOW)) + CRAWL_JOB_MARGIN_SEC


#: Паузы перед повторами задачи, упавшей на временном сбое: сеть, 5xx,
#: база. Три попытки сверху: полминуты на мигнувшую сеть, две минуты
#: на перезапуск провайдера, десять — на короткую аварию. Прогона это
#: не касается: его повторами владеет разбор мёртвых, и второй слой
#: повторов поверх него только запутал бы, кто что перезапустил.
#: ⚠ Паузы работают, только если воркер запущен с планировщиком
#: (`workers/main.py`): без него отложенные повторы копятся и не
#: срабатывают никогда — ровно так было в соседней системе.
RETRY_INTERVALS = (30, 120, 600)

#: Сколько хранится итог задачи. Умолчание rq — восемь минут, и человек,
#: открывший экран через час, не узнал бы, чем кончилась сборка.
RESULT_TTL = 7 * 24 * 60 * 60


#: Причина сбоя попытки, ушедшей на повтор. Отдельным ключом, а не в мете
#: задачи: rq при повторе сохраняет задачу своей копией и мету затирает
#: (проверено на 2.12), а исключения такой попытки не хранит вовсе.
JOB_ERROR_KEY = "outreach:job-error:{job_id}"


def remember_job_error(job_id: str, text: str, redis: Redis | None = None) -> None:
    """Запомнить причину сбоя задачи — чтобы «ждёт повтора» было с причиной."""
    try:
        (redis or connection()).set(JOB_ERROR_KEY.format(job_id=job_id), text[:500], ex=RESULT_TTL)
    except RedisError as exc:
        logger.warning("очередь: причину сбоя задачи %s не записать — %s", job_id, exc)


def job_error(job_id: str, redis: Redis | None = None) -> str | None:
    """Последняя запомненная причина сбоя задачи."""
    raw = (redis or connection()).get(JOB_ERROR_KEY.format(job_id=job_id))
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def parse_job_id(reply_id: int, message_id: str | None = None) -> str:
    """Номер задачи разбора: один на ответ.

    С ним постановка идемпотентна (`enqueue(..., unique=True)`): вебхук,
    повторённый платформой дважды, не ставит второй платный разбор, пока
    первый стоит в очереди, идёт или хранит итог. Хвост — от идентификатора
    письма: номера ответов после восстановления базы из копии начинаются
    заново, и новый ответ с номером старого не должен упереться в его давно
    выполненную задачу. Повтор того же письма даёт тот же хвост.
    """
    tail = hashlib.sha256(message_id.encode()).hexdigest()[:12] if message_id else "0"
    return f"parse-reply-{reply_id}-{tail}"


def sales_job_id(reply_id: int, message_id: str | None = None) -> str:
    """Номер задачи ответа продаж: один на ответ, по правилу `parse_job_id`."""
    return f"sales-{parse_job_id(reply_id, message_id)}"


def deeper_job_id(donor_id: int) -> str:
    """Номер поиска глубже — один на донора за раз (`api/letters/once.enqueue_once`)."""
    return f"contacts-deeper-{donor_id}"


def form_letter_job_id(donor_id: int) -> str:
    """Номер сборки текста письма для формы — один на донора за раз."""
    return f"form-letter-{donor_id}"


def crawl_job_id(run_id: int) -> str:
    """Номер задачи обхода — свой и заранее: он записывается в строку обхода
    до постановки, и задача, взятая раньше записи, с ней не разойдётся."""
    return f"crawl-{run_id}-{uuid.uuid4().hex[:8]}"


def enqueue_crawl(run_id: int, job_id: str | None = None) -> str:
    """Поставить обход в очередь обходов. Возвращает номер задачи."""
    job = crawl_queue().enqueue(
        CRAWL_JOB, run_id, job_id=job_id or crawl_job_id(run_id), result_ttl=RESULT_TTL
    )
    return str(job.id)


def with_retries() -> dict[str, Any]:
    """Параметры постановки для задач с повтором: всё, кроме прогона."""
    return {
        "retry": Retry(max=len(RETRY_INTERVALS), interval=list(RETRY_INTERVALS)),
        "result_ttl": RESULT_TTL,
    }


#: Сколько ждать Redis — подключения и ответа (аудит 10.10.2026, №9). Клиент синхронный,
#: а зовут его и маршруты единственного процесса API: без предела зависший Redis держал
#: первый же `GET /runs`, а с ним — всех людей и `/health`. Пять секунд — как у ленты
#: тревог сторожа (`ops/alarm_feed.py`). Воркеру короткий предел не мешает: на своё
#: блокирующее ожидание задачи (минуты) rq растягивает его сам (`Worker._set_connection`).
REDIS_TIMEOUT_SEC = 5.0


def connection() -> Redis:
    """Соединение с очередью. Адрес — из конфига, как и всё остальное; пределы ожидания —
    `REDIS_TIMEOUT_SEC`."""
    return Redis.from_url(
        storage.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SEC,
        socket_timeout=REDIS_TIMEOUT_SEC,
    )


def runs_queue(redis: Redis | None = None) -> Queue:
    return Queue(QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT)


def crawl_queue(redis: Redis | None = None) -> Queue:
    """Очередь обходов: её задачи — экран, консоль, продолжение после выкатки и разбор
    мёртвых — ставятся с одним потолком (`crawl_job_timeout`)."""
    return Queue(
        CRAWL_QUEUE_NAME, connection=redis or connection(), default_timeout=crawl_job_timeout()
    )


def sales_queue(redis: Redis | None = None) -> Queue:
    return Queue(SALES_QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT)


def sales_bulk_queue(redis: Redis | None = None) -> Queue:
    return Queue(
        SALES_BULK_QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT
    )


def quick_queue(redis: Redis | None = None) -> Queue:
    return Queue(QUICK_QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT)


def contacts_queue(redis: Redis | None = None) -> Queue:
    return Queue(CONTACTS_QUEUE_NAME, connection=redis or connection(), default_timeout=JOB_TIMEOUT)


#: Ключ живого воркера в Redis. Он держится heartbeat'ом самого rq
#: и исчезает вместе с процессом — это единственный признак, которому
#: можно верить.
WORKER_KEY_PREFIX = "rq:worker:"

#: Состояния, в которых задача считается живой сама по себе.
_LIVE_STATES = frozenset({"queued", "deferred", "scheduled"})


def workers_alive(redis: Redis | None = None, queue: str = QUEUE_NAME) -> int | None:
    """Сколько живых воркеров слушает очередь. `None` — не спросили.

    Нужно ровно для одного ответа человеку: «задача поставлена, и её
    некому взять». Без этого числа очередь без воркера выглядит как
    работающий сервис: сервер отвечает «поставлено», экран показывает
    успех, и дальше не происходит ничего — никогда и без единой записи.
    Проверено 21.09.2026: задача разбора провисела так сутки.
    """
    try:
        return len(Worker.all(queue=Queue(queue, connection=redis or connection())))
    except RedisError as exc:
        logger.warning("очередь: не удалось спросить, есть ли воркеры — %s", exc)
        return None


#: Где лежит номер последней задачи поиска контактов. Своей строки
#: в базе у неё нет намеренно: задача одна на сервис, идёт минутами
#: и не оставляет после себя ничего, кроме контактов у доноров и
#: отчёта в самой очереди.
CONTACTS_JOB_KEY = "outreach:contacts:job"

#: То же для рекламодателей Этапа 2. Ключ свой: под общим экран доноров
#: показал бы исход чужого поиска как свой.
ADVERTISER_CONTACTS_JOB_KEY = "outreach:advertiser-contacts:job"

#: Сколько помним номер задачи. Дольше её собственного срока хранения
#: смысла нет: отчёт всё равно исчезнет вместе с задачей.
CONTACTS_JOB_TTL = 24 * 60 * 60


def remember_contacts_job(
    job_id: str, redis: Redis | None = None, *, key: str = CONTACTS_JOB_KEY
) -> None:
    """Запомнить, какая задача сейчас ищет контакты.

    Не удалось — не беда: экран покажет «идёт» по самой очереди,
    а поиск от этого не остановится. Молчать об этом всё равно нельзя.
    """
    try:
        (redis or connection()).set(key, job_id, ex=CONTACTS_JOB_TTL)
    except RedisError:
        logger.warning("очередь: номер задачи поиска контактов не запомнен")


def contacts_job_id(redis: Redis | None = None, *, key: str = CONTACTS_JOB_KEY) -> str | None:
    """Номер последней задачи поиска контактов. `None` — очередь молчит."""
    try:
        raw = (redis or connection()).get(key)
    except RedisError:
        logger.warning("очередь: номер задачи поиска контактов не прочитан")
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


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
        conn = redis or connection()
        try:
            job = Job.fetch(job_id, connection=conn)
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
        return _worker_lives(job, conn)
    except RedisError as exc:
        logger.warning("очередь: не удалось узнать судьбу задачи %s — %s", job_id, exc)
        return None


def job_failure(job_id: str | None, redis: Redis | None = None) -> str | None:
    """Почему задача упала — строка самого исключения. `None` — не падала
    или узнать не вышло.

    Без этого разбор мёртвых прогонов пишет «воркер умер» и про задачу,
    которая честно упала с исключением. 24.09.2026 так выглядели прогоны
    №19 и №20: задача падала на MissingGreenlet, а в записи прогона стояло
    «воркер умер, прогон продолжен» — неправда вместо причины.
    """
    if not job_id:
        return None
    try:
        job = Job.fetch(job_id, connection=redis or connection())
        if job.get_status(refresh=True) != "failed":
            return None
        result = job.latest_result()
        trace = result.exc_string if result is not None else None
    except (NoSuchJobError, ValueError) as exc:
        logger.info("очередь: задачи %s нет — почему упала, не узнать (%s)", job_id, exc)
        return None
    except RedisError as exc:
        logger.warning("очередь: не удалось узнать, почему упала задача %s — %s", job_id, exc)
        return None
    return last_error_line(trace)


def last_error_line(trace: str | None) -> str | None:
    """Из трассировки — последняя непустая строка: само исключение, без стека."""
    lines = [line.strip() for line in (trace or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else None


def _worker_lives(job: Job, connection: Redis) -> bool:
    """Жив ли воркер, взявший задачу. Его ключ держится собственным
    heartbeat rq и исчезает вместе с процессом — в отличие от состояния
    задачи, которое так и остаётся «выполняется»."""
    owner = job.worker_name
    if not owner:
        return False
    return bool(connection.exists(f"{WORKER_KEY_PREFIX}{owner}"))
