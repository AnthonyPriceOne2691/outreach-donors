"""Наблюдаемость и отказы на стыках агента и продаж — ревью стыков 08.10, C1, C2, G1, G2.

Все тесты здесь — пробелы общего кода, помечены `xfail(strict=True)`: правку делает PR «общее»,
её точный текст — в отчёте ревью (R3). Правка сделана — тест проходит, `strict` красит набор,
и пометку снимают вместе с правкой.

- **C1, C2 — строка при старте.** Процесс, который пишет черновики агента и тратит модель,
  говорит при старте словами: включён ли агент продаж (`SALES_AGENT_ENABLED`), есть ли общий
  дневной потолок модели (`LLM_DAILY_TOKEN_CAP`, на рабочем сервере сейчас 0 — потолка нет, и
  у черновиков тоже), и не больше ли свой потолок черновиков (`AGENT_DAILY_TOKEN_CAP`) доли
  общего, ради которой он заведён.
- **G1 — отказ словами.** «Отклонить черновик» без причины — 422 словами, а не списком полей
  проверки на английском; сборка очереди продаж при недоступной очереди задач — 503 словами,
  а не «голая» 500.
- **G2 — Redis в тестах.** Набор тестов не может дотянуться до Redis из настроек: по умолчанию
  это общий Redis машины, и задача, поставленная тестом без подставной очереди, ушла бы чужому
  воркеру. Сейчас каждый тест подменяет очередь сам (аудит набора агента и продаж — чисто), но
  общей страховки нет.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import redis
from backend.api.errors import QUEUE_DOWN
from backend.config import llm as llm_cfg
from backend.config.storage import _Storage
from backend.features.core.domain import UserRole
from backend.shared import queue as shared_queue
from backend.workers import main as worker_main
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_queue_api import QUEUE, headers, world

__all__ = ["headers", "sales_on", "world"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[Any]]
SignIn = Callable[..., Awaitable[str]]
#: Выдуманные потолки: общий и свой, свой — больше доли общего (0,3).
GENERAL, OWN = 70913, 41207


class _Worker:
    """Воркер очереди без Redis: запоминает, что его запустили."""

    def __init__(self, queues: list[str], connection: object) -> None:
        self.queues = queues

    def work(self, with_scheduler: bool) -> None:
        return None


def _started(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> str:
    """Старт воркера очереди продаж — и всё, что он сказал в журнал."""
    monkeypatch.setattr(worker_main, "Worker", _Worker)
    monkeypatch.setattr(worker_main, "check_storage", lambda: None)
    monkeypatch.setattr(worker_main, "connection", lambda: None)
    monkeypatch.setattr(worker_main, "setup_logging", lambda: None)
    with caplog.at_level(logging.INFO):
        worker_main.main(["--queue", "sales"])
    return "\n".join(record.getMessage() for record in caplog.records)


# --- C1, C2: строка при старте ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("general", "own", "named"),
    [(0, None, "LLM_DAILY_TOKEN_CAP"), (GENERAL, OWN, "AGENT_DAILY_TOKEN_CAP")],
    ids=["no-general-cap", "own-above-the-share"],
)
def test_c2_start_says_in_words_what_caps_the_drafts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    general: int,
    own: int | None,
    named: str,
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", own)

    assert named in _started(monkeypatch, caplog)


@pytest.mark.usefixtures("sales_on")
def test_c1_start_says_in_words_that_the_sales_agent_is_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    assert "SALES_AGENT_ENABLED" in _started(monkeypatch, caplog)


# --- G1: отказ словами -----------------------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"reason": "   "}], ids=["no-reason", "blank-reason"])
async def test_g1_reject_without_a_reason_is_422_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, body: dict[str, str]
) -> None:
    await make_user("admin@seam-r3.example.test", role=UserRole.ADMIN)
    signed = bearer(await sign_in("admin@seam-r3.example.test"))

    refused = await client.post("/api/agent/drafts/7/reject", json=body, headers=signed)

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert isinstance(detail, str), detail
    assert "причин" in detail


class _Down:
    """Очередь задач, у которой лежит Redis: и проверка идущей сборки, и постановка."""

    def fetch_job(self, _job_id: str) -> object:
        raise RedisConnectionError("Error 61 connecting to localhost:6389. Connection refused.")

    def enqueue(self, *_args: object, **_kwargs: object) -> object:
        raise RedisConnectionError("Error 61 connecting to localhost:6389. Connection refused.")


async def test_g1_sales_queue_build_with_the_job_queue_down_is_503_in_words(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.api.sales.queue.sales_queue", _Down)
    await session.commit()

    response = await client.post(
        QUEUE, json={"hypothesis_id": world.hypothesis_id, "limit": 13}, headers=headers
    )

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": QUEUE_DOWN}  # адрес и номер ошибки redis-py — в журнал


# --- G2: Redis в тестах ------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="общий код: tests/conftest.py не отгораживает набор от Redis из настроек (общий Redis "
    "машины) — правка в отчёте R3, G2",
)
def test_g2_the_suite_cannot_reach_the_redis_of_the_settings() -> None:
    """Без сети: клиент redis-py соединяется только командой, адрес читается из пула."""
    configured = redis.Redis.from_url(_Storage().redis_url).connection_pool.connection_kwargs
    try:
        client = shared_queue.connection()
    except RedisError as exc:  # страховка набора: соединение из настроек в тесте — отказ
        logging.getLogger(__name__).info("очередь в тесте отказана", extra={"why": str(exc)})
        return
    reached = client.connection_pool.connection_kwargs
    assert [reached.get(key) for key in ("host", "port", "db")] != [
        configured.get(key) for key in ("host", "port", "db")
    ]
