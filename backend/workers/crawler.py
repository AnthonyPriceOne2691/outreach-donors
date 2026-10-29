"""Сервис обходов: `python -m backend.workers.crawler`.

Свой процесс со своей очередью (`crawl`), а не общий воркер: обход донора идёт
до часа (сайт с долгой паузой — до трёх), и в общей очереди он занял бы
единственный воркер — разбор ответов и сборка писем ждали бы его.

**Сколько доноров идут разом — числом контейнеров** (`CRAWL_WORKERS`, по
умолчанию 16): в каждом один воркер rq и одна задача за раз, у каждого свои
лимиты памяти и процессора. Несколько процессов в одном контейнере делили бы
один лимит, и тяжёлый донор с браузером душил бы соседей.

**И свободными ядрами машины** (решение Anthony 29.10.2026): прежде чем взять
следующую задачу, копия спрашивает сторожа (`workers/load_gate.py`) — места под
ещё один обход нет, копия ждёт, не беря работы; идущий обход сторож не трогает.
Сосед занял ядра — обходов идёт, сколько влезает; свободно всё — идут все 16.

**Выкатка не обрывает обход.** Докер просит остановиться (SIGTERM) и ждёт
`stop_grace_period`, потом убивает. rq по такой просьбе дожидается задачи, но
ей самой ничего не говорит — и обход умирал бы посреди страницы. Здесь просьба
передаётся процессу задачи: обход дописывает пачку на границе страницы
и ставит себе продолжение (`workers/crawl_jobs.py`). Копия, которую держит
сторож, задачи не имеет: просьба останавливает её прямо в ожидании.

Планировщика rq здесь нет: отложенных задач у обхода нет, а планировщик
общей очереди держит воркер `worker`.
"""

from __future__ import annotations

import logging
import os
import signal

from rq import Queue, Worker
from rq.job import Job
from rq.worker import WorkerStatus

from backend.config.startup_checks import check_crawl, check_storage
from backend.shared.logs import setup_logging
from backend.shared.queue import connection, crawl_queue
from backend.workers import crawl_jobs, load_gate

logger = logging.getLogger(__name__)


class CrawlWorker(Worker):
    """Воркер rq, который передаёт просьбу остановиться задаче и не берёт новую,
    пока машина занята."""

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

    def dequeue_job_and_maintain_ttl(
        self,
        timeout: int | None,
        max_idle_time: int | None = None,  # noqa: ARG002 — подпись rq; обходчик его не задаёт
    ) -> tuple[Job, Queue] | None:
        """Следующая задача — только под свободные ядра машины (`load_gate`).

        rq ждёт задачу в блокирующем чтении очереди, и сторожа перед самым взятием там не
        спросить: задача, пришедшая через полчаса простоя, судилась бы машиной получасовой
        давности. Поэтому очередь здесь смотрится раз в `QUEUE_POLL_SEC`, а берётся
        неблокирующим чтением сразу после сторожа. Пустая — копия ждёт задачу, а не машину
        (и не отмечается «ждёт»); только что начала соседняя копия — ждёт конца её отметки;
        машина занята — ждёт места. Разовый режим (`timeout=None`) с пустой очередью — конец,
        как у rq; `max_idle_time` обходчик не задаёт (`main`)."""
        self.set_state(WorkerStatus.IDLE)
        while True:
            if self._stop_requested:
                return None
            if not load_gate.work_queued(self):
                if timeout is None:
                    return None
                load_gate.pause(self, load_gate.QUEUE_POLL_SEC)
                continue
            if load_gate.just_started(self):
                load_gate.pause(self, load_gate.QUEUE_POLL_SEC)
                continue
            load_gate.wait_for_room(self)
            result = super().dequeue_job_and_maintain_ttl(None)
            if result is not None:
                load_gate.took_work(self)
                return result


def main() -> None:
    setup_logging()
    check_storage()
    # Битый адрес прокси или шаблон сервиса — отказ на старте словами, без секретов.
    check_crawl()
    CrawlWorker([crawl_queue()], connection=connection()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
