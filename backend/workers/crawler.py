"""Сервис обходов: `python -m backend.workers.crawler`.

Свой процесс со своей очередью (`crawl`), а не общий воркер: обход донора идёт
до получаса, и в общей очереди он занял бы единственный воркер — разбор ответов
и сборка писем ждали бы его.

**Сколько доноров идут разом — числом контейнеров** (`CRAWL_WORKERS`, по
умолчанию 4): в каждом один воркер rq и одна задача за раз, у каждого свои
лимиты памяти и процессора. Несколько процессов в одном контейнере делили бы
один лимит, и тяжёлый донор с браузером душил бы соседей.

**Выкатка не обрывает обход.** Докер просит остановиться (SIGTERM) и ждёт
`stop_grace_period`, потом убивает. rq по такой просьбе дожидается задачи, но
ей самой ничего не говорит — и обход умирал бы посреди страницы. Здесь просьба
передаётся процессу задачи: обход дописывает пачку на границе страницы
и ставит себе продолжение (`workers/crawl_jobs.py`).

Планировщика rq здесь нет: отложенных задач у обхода нет, а планировщик
общей очереди держит воркер `worker`.
"""

from __future__ import annotations

import logging
import os
import signal

from rq import Worker

from backend.config.startup_checks import check_storage
from backend.shared.logs import setup_logging
from backend.shared.queue import connection, crawl_queue
from backend.workers import crawl_jobs

logger = logging.getLogger(__name__)


class CrawlWorker(Worker):
    """Воркер rq, который передаёт просьбу остановиться задаче."""

    def setup_work_horse_signals(self) -> None:
        """Процесс задачи: Ctrl+C не про него (как у rq), а SIGTERM — не смерть,
        а «остановись на границе страницы»: флаг читает обход перед каждой
        страницей. У rq здесь смерть по умолчанию."""
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, crawl_jobs.ask_to_stop)

    def handle_warm_shutdown_request(self) -> None:
        """Первая просьба остановиться — задаче тоже: иначе она узнает
        о выкатке только от SIGKILL докера. У rq здесь только строка журнала."""
        horse = self.horse_pid
        if not horse:
            logger.info("обходчик: просьба остановиться, задачи нет — выхожу")
            return
        logger.info("обходчик: просьба остановиться передана задаче (процесс %s)", horse)
        os.kill(horse, signal.SIGTERM)


def main() -> None:
    setup_logging()
    check_storage()
    CrawlWorker([crawl_queue()], connection=connection()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
