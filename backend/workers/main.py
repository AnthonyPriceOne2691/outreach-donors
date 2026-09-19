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
    Worker([QUEUE_NAME], connection=connection()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
