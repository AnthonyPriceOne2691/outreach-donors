"""Очереди воркеров: у каждого сервиса своя, и проверка здоровья знает их все.

Разбор скорости 14.10.2026: короткие задачи (разбор ответа, черновик агента,
передача лида, пересчёт после обхода) и поиск адресов стояли в общей очереди за
прогоном доноров до часа. Теперь у них свои очереди и свои процессы.
"""

from __future__ import annotations

import pytest
from backend.shared import queue
from backend.workers import health
from backend.workers.main import queue_of


@pytest.mark.parametrize(
    ("argv", "name"),
    [
        ([], queue.QUEUE_NAME),
        (["--queue", "quick"], queue.QUICK_QUEUE_NAME),
        (["--queue", "contacts"], queue.CONTACTS_QUEUE_NAME),
    ],
)
def test_worker_listens_to_the_queue_it_is_given(argv: list[str], name: str) -> None:
    assert queue_of(argv) == name


def test_health_knows_every_worker_queue() -> None:
    """Сервис без строки здесь отвечал бы «что проверять?» и считался бы больным."""
    assert health.WORKER_QUEUES["quick"] == queue.QUICK_QUEUE_NAME
    assert health.WORKER_QUEUES["contacts"] == queue.CONTACTS_QUEUE_NAME


def test_short_jobs_and_contacts_have_their_own_queues() -> None:
    names = {
        queue.QUEUE_NAME,
        queue.CRAWL_QUEUE_NAME,
        queue.SALES_QUEUE_NAME,
        queue.SALES_BULK_QUEUE_NAME,
        queue.QUICK_QUEUE_NAME,
        queue.CONTACTS_QUEUE_NAME,
    }
    assert len(names) == 6, "у каждой очереди своё имя"
    redis = queue.connection()
    assert queue.quick_queue(redis).name == "quick"
    assert queue.contacts_queue(redis).name == "contacts"
