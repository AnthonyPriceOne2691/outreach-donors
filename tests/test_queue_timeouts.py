"""Зависший Redis не останавливает сервер (аудит 10.10.2026, №9).

Клиент очереди синхронный, а зовут его маршруты единственного процесса API. Без предела
ожидания зависший Redis держал первый же `GET /api/runs` — а с ним цикл событий: всех
людей и `/health`. Теперь у клиента пределы подключения и ответа, а маршруты ждут очередь
в пуле потоков.

Redis здесь — настоящий сокет, который принимает соединение и молчит: так выглядит
зависший. Через `HOLD` секунд он закрывает соединение — без предела клиент ждал бы ровно
до этого закрытия.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Awaitable, Callable, Iterator

import pytest
from backend.api.errors import QUEUE_DOWN
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.shared import queue
from httpx import AsyncClient, Response
from rq import Worker
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Сколько молчащий Redis держит соединение, прежде чем закрыть.
HOLD = 2.0
#: Предел ожидания клиента в тестах — заметно меньше `HOLD`.
LIMIT = 0.3


@pytest.fixture
def silent_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Адрес Redis, который принял соединение и молчит `HOLD` секунд."""
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(0.05)
    held: list[tuple[socket.socket, float]] = []
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                taken, _ = server.accept()
                held.append((taken, time.monotonic()))
            except TimeoutError:
                pass
            for taken, since in list(held):
                if time.monotonic() - since > HOLD:
                    taken.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    url = f"redis://127.0.0.1:{server.getsockname()[1]}/0"
    monkeypatch.setattr("backend.config.storage.REDIS_URL", url)
    monkeypatch.setattr(queue, "REDIS_TIMEOUT_SEC", LIMIT)
    yield url
    stop.set()
    thread.join()
    for taken, _ in held:
        taken.close()
    server.close()


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


def test_the_queue_client_has_limits() -> None:
    """Пределы — у того клиента, которым ходят в очередь все: маршруты, сторож, задачи."""
    kwargs = queue.connection().connection_pool.connection_kwargs

    assert kwargs["socket_timeout"] == queue.REDIS_TIMEOUT_SEC
    assert kwargs["socket_connect_timeout"] == queue.REDIS_TIMEOUT_SEC


def test_asking_a_silent_redis_gives_up_in_time(silent_redis: str) -> None:
    """«Есть ли воркеры» и «жива ли задача» — со своим клиентом: и он с пределом."""
    started = time.monotonic()
    workers = queue.workers_alive()
    alive = queue.job_alive("run-1")

    assert (workers, alive) == (None, None), "не знаю — а не «никого» и не «мертва»"
    assert time.monotonic() - started < HOLD, "ждали молчащий Redis до его закрытия"


def test_the_worker_waits_for_jobs_longer_than_the_limit() -> None:
    """Короткий предел не режет воркер: своё блокирующее ожидание задачи (минуты) rq
    растягивает сам — у соединения воркера предел не меньше этого ожидания."""
    worker = Worker([queue.QUEUE_NAME], connection=queue.connection(), prepare_for_work=False)

    kwargs = worker.connection.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] >= worker.dequeue_timeout > queue.REDIS_TIMEOUT_SEC


async def _with_ticks(asked: Awaitable[Response]) -> tuple[Response, int]:
    """Ответ маршрута и сколько раз за это время успел провернуться цикл событий."""
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        response = await asyncio.wait_for(asked, timeout=10)
    finally:
        ticker.cancel()
    return response, ticks


async def test_runs_page_does_not_stop_the_server(
    client: AsyncClient, token: str, silent_redis: str
) -> None:
    """Пока маршрут ждёт Redis, цикл событий работает: остальные запросы идут."""
    listed, ticks = await _with_ticks(client.get("/api/runs", headers=bearer(token)))

    assert listed.status_code == 200, listed.text
    assert listed.json()["workers"] is None
    assert ticks >= 10, f"цикл событий стоял, пока маршрут ждал Redis: {ticks} оборотов"


async def test_start_does_not_stop_the_server_and_says_why(
    client: AsyncClient, token: str, silent_redis: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Постановка прогона в молчащий Redis — 503 словами, и цикл событий не стоял."""
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)

    started, ticks = await _with_ticks(
        client.post(
            "/api/runs",
            json={"keywords": ["ремонт квартир"], "country": "us"},
            headers=bearer(token),
        )
    )

    assert started.status_code == 503, started.text
    assert started.json()["detail"] == QUEUE_DOWN
    assert ticks >= 10, f"цикл событий стоял, пока ставили задачу: {ticks} оборотов"
