"""Разбор мёртвых прогонов: `python -m backend.workers.reaper`.

Отдельный процесс, а не работа внутри сервера или воркера, и на то две
причины. Серверов бывает несколько — каждый вёл бы свой разбор и ставил
бы свою задачу тому же прогону. А воркер, который и умирает, разобрать
собственную смерть не может.

Цикл простой: раз в `POLL_INTERVAL_SEC` спросить базу, кто давно молчит,
и по каждому — Redis: жива ли его задача. Правила живут в ядре
(`features/runs/lifecycle`), здесь только проводка: сессия, очередь,
интервал и то, что процесс не должен падать целиком из-за одного
неудачного прохода.

Третий цикл — повтор передачи лидов продаж (`features/sales/handoff_jobs.retry_pass`):
передачи, которые Kommo не принял, и задачи, потерянные очередью. Своего
контейнера ему не заводим — ему, как и сторожу, нужна сессия раз в несколько минут.
Модуль продаж цикл импортирует сам, при первом проходе: сбой его импорта — сбой
этого цикла (его ловит `every`), а разбор прогонов и сторож идут своим чередом.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.crawl.lifecycle import recover as recover_crawls
from backend.features.ops import silence
from backend.features.ops.alarm_feed import Feed
from backend.features.runs.lifecycle import Recovery, recover
from backend.features.runs.repository import RunRepository
from backend.shared.logs import setup_logging
from backend.shared.queue import RUN_JOB, enqueue_crawl, job_alive, job_failure, runs_queue
from backend.workers.ticker import every

logger = logging.getLogger(__name__)

#: Как часто ходить за мёртвыми. Кратно меньше порога похорон, чтобы
#: между смертью и её признанием прошло не больше пары проходов, и
#: заметно больше удара о жизни, чтобы не будить базу впустую.
POLL_INTERVAL_SEC = 60.0

#: Как часто сторож смотрит на тишину. Реже разбора намеренно: его
#: пороги измеряются часами, и спрашивать базу каждую минуту незачем.
WATCHDOG_INTERVAL_SEC = 600.0

#: Что сторож уже сказал человеку в Telegram — по смене состояния (`ops/alarm_feed.py`).
FEED = Feed()


def _enqueue(run_id: int) -> str | None:
    """Поставить прогону новую задачу. `None` — очередь не ответила.

    Молчание очереди здесь не ошибка процесса: прогон останется
    неразобранным до следующего прохода, и это честнее, чем считать
    его закрытым.
    """
    try:
        return str(runs_queue().enqueue(RUN_JOB, run_id).id)
    except Exception:
        logger.exception("Разбор: прогон %s продолжить не удалось — очередь не ответила", run_id)
        return None


def _enqueue_crawl(run_id: int) -> str | None:
    """Продолжение обхода — в очередь обходов. `None` — очередь не ответила."""
    try:
        return enqueue_crawl(run_id)
    except Exception:
        logger.exception("Разбор: обход %s продолжить не удалось — очередь не ответила", run_id)
        return None


async def sweep() -> None:
    """Один проход разбора: прогоны Этапа 1 и обходы Этапа 2. Своя сессия
    на проход: процесс живёт сутками, а сессия, живущая столько же, видит
    базу такой, какой она была при её открытии."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            _told(
                "Разбор прогонов",
                await recover(
                    RunRepository(session), alive=job_alive, enqueue=_enqueue, failure=job_failure
                ),
            )
        async with factory() as session:
            _told(
                "Разбор обходов",
                await recover_crawls(
                    session, alive=job_alive, enqueue=_enqueue_crawl, failure=job_failure
                ),
            )
    finally:
        await engine.dispose()


def _told(what: str, outcome: Recovery) -> None:
    """Строка журнала — только если проход что-то сделал или не выяснил."""
    if outcome.resumed or outcome.stopped or outcome.unknown:
        logger.info("%s: %s", what, outcome.as_report)


async def watch() -> None:
    """Один проход сторожа тишины.

    Живёт в этом же процессе, а не в своём: сторож ничего не чинит
    и никого не будит — ему нужна только сессия раз в несколько минут.
    Отдельный процесс ради одного запроса к базе — это ещё один
    контейнер, который однажды не поднимется, и тогда молчать будет
    уже сам сторож.

    Сессия — только на чтение тревог. Опрос провайдеров и лента тревог
    (Telegram, Redis) идут после её закрытия: при открытой транзакции
    соединение висело бы «idle in transaction» на время чужих ответов.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            found = await silence.alarms(session)
    finally:
        await engine.dispose()
    await FEED.tell(await silence.with_providers(found))


async def retry_handoffs() -> None:
    """Проход повторов передачи лидов продаж. Импорт модуля продаж — здесь, а не при
    загрузке процесса: упадёт он — упадёт этот цикл, а не весь разбор."""
    from backend.features.sales.handoff_jobs import retry_pass  # noqa: PLC0415 — лениво

    await retry_pass()


async def _loops() -> None:
    """Проходы с разными интервалами в одном процессе. Падение
    одного не должно останавливать другие — этим занимается `every`."""
    await asyncio.gather(
        every(POLL_INTERVAL_SEC, sweep, name="Разбор мёртвых прогонов"),
        every(WATCHDOG_INTERVAL_SEC, watch, name="Сторож тишины"),
        every(sales_cfg.HANDOFF_PASS_SEC, retry_handoffs, name="Повтор передачи лидов продаж"),
    )


def main() -> None:
    setup_logging()
    check_storage()
    asyncio.run(_loops())


if __name__ == "__main__":
    main()
