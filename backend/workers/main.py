"""Точка запуска воркера: `python -m backend.workers.main`.

Воркер поднимается той же проверкой конфига, что и сервер: процесс,
которому не хватает настройки, обязан падать сразу и называть, чего
не хватает. Иначе задача берётся из очереди, падает на первом же
обращении к базе и возвращается в очередь — и так по кругу, молча.
"""

from __future__ import annotations

from rq import Worker

from backend.config.startup_checks import check_storage
from backend.shared.logs import setup_logging
from backend.shared.queue import QUEUE_NAME, connection


def main() -> None:
    setup_logging()
    check_storage()
    # С планировщиком: без него повторы задач с паузой (`queue.RETRY_INTERVALS`)
    # копятся отложенными и не срабатывают никогда. Планировщик на очередь
    # один — rq держит его замком, и второй воркер его не запустит.
    Worker([QUEUE_NAME], connection=connection()).work(with_scheduler=True)


if __name__ == "__main__":
    main()
