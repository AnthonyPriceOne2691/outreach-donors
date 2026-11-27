"""Задача прохода, чей воркер умер, убирается вместе со своими выполнениями rq 2.12.

Прод 27.11: сервисы пересоздали посреди прохода прогона, воркер умер. Запись его выполнения
(`rq:execution:…`) живёт пульсом, строка о нём в реестре выполнений задачи — на 60 с дольше:
1) проход конвейера плана попал в эту минуту — `Job.delete()` споткнулся о строку без записи
   (ValueError «Execution … not found in Redis»), и проход упал;
2) следующий проход задачу убрал, но строка выполнения в реестре идущих (`rq:wip:<очередь>`)
   осталась: `delete()` снимает её только у выполнений, чьи записи нашлись. Обслуживание rq взяло
   из неё номер задачи, нашло новую задачу под тем же номером — «брошена» — и поставило её
   повтором: проход прогона прошёл дважды.

Здесь — настоящие классы rq (задача, выполнение, реестры, обслуживание) на Redis в памяти:
команды — ровно те, что rq зовёт на этих путях; сроков ключей нет — истёкшую запись выполнения
тест стирает сам, а срок строки в реестре задаёт пульс выполнения (`ttl`).
"""

from __future__ import annotations

import time
from typing import Any, cast

import pytest
from backend.features.sales import segment_watch
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.shared.queue import SALES_BULK_QUEUE_NAME, WORKER_KEY_PREFIX
from redis import Redis
from redis.client import Pipeline
from rq import Queue
from rq.executions import Execution
from rq.job import Job, JobStatus
from rq.registry import StartedJobRegistry


def _bytes(value: Any) -> bytes:
    """Как кодирует redis-py: байты — как есть, строка — utf-8, число — его запись."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return repr(value).encode() if isinstance(value, float) else str(value).encode()


class _Redis:
    """Redis в памяти: хэши, сортированные множества, списки и строки; конвейер исполняет
    команды сразу."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.zsets: dict[str, dict[bytes, float]] = {}
        self.lists: dict[str, list[bytes]] = {}
        self.plain: dict[str, bytes] = {}

    def _stores(self) -> tuple[dict[str, Any], ...]:
        return self.hashes, self.zsets, self.lists, self.plain

    def pipeline(self, transaction: bool = True) -> _Redis:
        return self

    def execute(self) -> list[object]:
        return []

    def __enter__(self) -> _Redis:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def hset(
        self,
        name: str,
        key: Any = None,
        value: Any = None,
        mapping: dict[Any, Any] | None = None,
    ) -> int:
        fields = dict(mapping or {})
        if key is not None:
            fields[key] = value
        target = self.hashes.setdefault(name, {})
        for field, raw in fields.items():
            target[_bytes(field)] = _bytes(raw)
        return len(fields)

    def hget(self, name: str, key: Any) -> bytes | None:
        return self.hashes.get(name, {}).get(_bytes(key))

    def hgetall(self, name: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(name, {}))

    def expire(self, *_args: object, **_options: object) -> bool:
        return True

    def set(self, name: str, value: Any, **_options: object) -> bool:
        self.plain[name] = _bytes(value)
        return True

    def exists(self, *names: str) -> int:
        return sum(1 for name in names if any(name in store for store in self._stores()))

    def delete(self, *names: str) -> int:
        return sum(
            1 for name in names for store in self._stores() if store.pop(name, None) is not None
        )

    def lrem(self, name: str, _count: int, value: Any) -> int:
        items = self.lists.get(name, [])
        kept = [item for item in items if item != _bytes(value)]
        self.lists[name] = kept
        return len(items) - len(kept)

    def zadd(self, name: str, mapping: dict[Any, Any], xx: bool = False, **_options: object) -> int:
        target = self.zsets.setdefault(name, {})
        added = 0
        for member, score in mapping.items():
            key = _bytes(member)
            if xx and key not in target:
                continue
            added += key not in target
            target[key] = float(score)
        return added

    def zrem(self, name: str, *members: Any) -> int:
        target = self.zsets.get(name, {})
        return sum(1 for member in members if target.pop(_bytes(member), None) is not None)

    def _ordered(self, name: str, desc: bool = False) -> list[tuple[bytes, float]]:
        items = self.zsets.get(name, {}).items()
        return sorted(items, key=lambda item: item[1], reverse=desc)

    def zrange(
        self, name: str, start: int, end: int, desc: bool = False, **_options: object
    ) -> list[bytes]:
        members = [member for member, _score in self._ordered(name, desc)]
        return members[start:] if end == -1 else members[start : end + 1]

    def zrangebyscore(self, name: str, low: float, high: float, **_options: object) -> list[bytes]:
        return [
            member for member, score in self._ordered(name) if float(low) <= score <= float(high)
        ]

    def zremrangebyscore(self, name: str, low: float, high: float) -> int:
        gone = self.zrangebyscore(name, low, high)
        return self.zrem(name, *gone)


NUMBER = segment_job_id(1)


def _client(redis: _Redis) -> Redis:
    """Подделка там, где rq ждёт клиента Redis."""
    return cast("Redis", redis)


@pytest.fixture
def redis() -> _Redis:
    return _Redis()


@pytest.fixture
def jobs(redis: _Redis) -> Queue:
    return Queue(SALES_BULK_QUEUE_NAME, connection=_client(redis))


def _taken(redis: _Redis, number: str, worker: str, ttl: int) -> Job:
    """Задача прохода (как её ставит `deferred.once`: с повторами), которую взял воркер `worker`:
    «идёт», выполнение с пульсом `ttl` — в реестре выполнений задачи и в реестре идущих."""
    job = Job.create(
        SEGMENT_JOB, args=(1,), connection=_client(redis), id=number, origin=SALES_BULK_QUEUE_NAME
    )
    job.retries_left, job.retry_intervals = 3, [60]
    job.worker_name = worker
    job.save()
    job.set_status(JobStatus.STARTED)
    Execution.create(job, ttl, pipeline=cast("Pipeline", redis))
    return job


def _records_expired(redis: _Redis) -> None:
    """Записи выполнений истекли: их пульс кончился вместе с воркером."""
    redis.hashes = {
        key: value for key, value in redis.hashes.items() if not key.startswith("rq:execution:")
    }


def _lines(redis: _Redis, number: str) -> list[str]:
    """Строки задачи `number` в реестре идущих очереди."""
    started = StartedJobRegistry(SALES_BULK_QUEUE_NAME, connection=_client(redis))
    return [
        execution
        for job_id, execution in started.get_job_and_execution_ids(cleanup=False)
        if job_id == number
    ]


@pytest.mark.parametrize(
    "ttl",
    [
        pytest.param(2, id="в минуту, пока строка реестра выполнений жива"),
        pytest.param(-61, id="минутой позже: строка реестра выполнений истекла"),
    ],
)
def test_a_dead_job_goes_with_its_executions_and_the_next_one_is_not_abandoned(
    redis: _Redis, jobs: Queue, ttl: int
) -> None:
    dead = _taken(redis, NUMBER, "умерший", ttl)  # ключа воркера нет — он умер
    _records_expired(redis)
    neighbour = _taken(redis, segment_job_id(2), "соседний", ttl=600)
    redis.set(f"{WORKER_KEY_PREFIX}соседний", 1)

    assert segment_watch.drop_dead_job(jobs, NUMBER)

    assert not redis.exists(dead.key, f"rq:executions:{NUMBER}")
    assert _lines(redis, NUMBER) == []
    assert len(_lines(redis, neighbour.id)) == 1  # чужие строки на месте

    # Проход ставит задачу снова под тем же номером, её берёт живой воркер; обслуживание rq —
    # через две минуты: строка умершего выполнения была бы уже просрочена.
    redis.set(f"{WORKER_KEY_PREFIX}живой", 1)
    again = _taken(redis, NUMBER, "живой", ttl=600)
    StartedJobRegistry(SALES_BULK_QUEUE_NAME, connection=_client(redis)).cleanup(time.time() + 120)

    assert again.get_status(refresh=True) == JobStatus.STARTED
    assert neighbour.get_status(refresh=True) == JobStatus.STARTED


def test_a_job_whose_worker_lives_keeps_its_executions(redis: _Redis, jobs: Queue) -> None:
    redis.set(f"{WORKER_KEY_PREFIX}живой", 1)
    going = _taken(redis, NUMBER, "живой", ttl=600)

    assert not segment_watch.drop_dead_job(jobs, NUMBER)

    assert going.get_status(refresh=True) == JobStatus.STARTED
    assert len(_lines(redis, NUMBER)) == 1
