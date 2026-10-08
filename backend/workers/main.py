"""Точка запуска воркера: `python -m backend.workers.main [--queue sales]`.

Воркер поднимается той же проверкой конфига, что и сервер: процесс,
которому не хватает настройки, обязан падать сразу и называть, чего
не хватает. Иначе задача берётся из очереди, падает на первом же
обращении к базе и возвращается в очередь — и так по кругу, молча.

**Очередь — доводом, одна на процесс.** Ответы продаж идут своей очередью
(`sales`) и своим сервисом (`worker-sales`): один воркер на две очереди
не помог бы — запущенный часовой прогон держит процесс целиком.
"""

from __future__ import annotations

import argparse
import sys

from rq import Worker

from backend.config.startup_checks import check_storage
from backend.features.agent.guarding import said_at_start
from backend.shared.logs import setup_logging
from backend.shared.queue import QUEUE_NAME, SALES_QUEUE_NAME, connection


def queue_of(argv: list[str]) -> str:
    """Какую очередь слушать. Незнакомое имя — отказ argparse: воркер
    на очереди, в которую никто не ставит, молчал бы вечно."""
    parser = argparse.ArgumentParser(prog="python -m backend.workers.main")
    parser.add_argument("--queue", choices=(QUEUE_NAME, SALES_QUEUE_NAME), default=QUEUE_NAME)
    return str(parser.parse_args(argv).queue)


def main(argv: list[str] | None = None) -> None:
    name = queue_of(sys.argv[1:] if argv is None else argv)
    setup_logging()
    check_storage()
    # Черновики агента и расход модели воркер ведёт задачами: что включено и чем
    # ограничено — словами в журнал при старте, а не только в `.env`.
    said_at_start()
    # С планировщиком: без него повторы задач с паузой (`queue.RETRY_INTERVALS`)
    # копятся отложенными и не срабатывают никогда. Планировщик держит замок
    # на свою очередь: у `runs` и у `sales` он у каждой свой.
    Worker([name], connection=connection()).work(with_scheduler=True)


if __name__ == "__main__":
    main()
