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
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.runs.lifecycle import recover
from backend.features.runs.repository import RunRepository
from backend.shared.logs import setup_logging
from backend.shared.queue import RUN_JOB, job_alive, runs_queue
from backend.workers.ticker import every

logger = logging.getLogger(__name__)

#: Как часто ходить за мёртвыми. Кратно меньше порога похорон, чтобы
#: между смертью и её признанием прошло не больше пары проходов, и
#: заметно больше удара о жизни, чтобы не будить базу впустую.
POLL_INTERVAL_SEC = 60.0


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


async def sweep() -> None:
    """Один проход разбора. Своя сессия на проход: процесс живёт сутками,
    а сессия, живущая столько же, видит базу такой, какой она была при
    её открытии."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            outcome = await recover(RunRepository(session), alive=job_alive, enqueue=_enqueue)
        if outcome.resumed or outcome.stopped or outcome.unknown:
            logger.info("Разбор прогонов: %s", outcome.as_report)
    finally:
        await engine.dispose()


def main() -> None:
    setup_logging()
    check_storage()
    asyncio.run(every(POLL_INTERVAL_SEC, sweep, name="Разбор мёртвых прогонов"))


if __name__ == "__main__":
    main()
