"""Очередь и живость: то, на что смотрит контейнер.

Проверяется не «работает ли rq» — это чужой код, — а наши договорённости
с ним: имя очереди одно на весь проект, задача передаётся путём
к функции, а её путь действительно импортируется. Разъехавшееся имя
не роняет ничего: сервер кладёт задачу в одну очередь, воркер слушает
другую, и прогон просто не случается.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.config.startup_checks import ConfigError, check_collect
from backend.shared.queue import JOB_TIMEOUT, QUEUE_NAME
from backend.workers import jobs
from backend.workers.main import QUEUE_NAME as LISTENED_QUEUE
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


class TestQueueContract:
    def test_worker_listens_to_the_same_queue(self) -> None:
        """Имя берут из одного места и сервер, и воркер."""
        assert LISTENED_QUEUE == QUEUE_NAME

    def test_job_is_importable_by_path(self) -> None:
        """Задача уходит в очередь путём к функции. Если путь не импортируется,
        выяснится это у воркера в проде — а не здесь."""
        assert callable(jobs.run_donor_search)

    def test_timeout_outlives_a_real_run(self) -> None:
        """Умолчание rq — три минуты, а прогон идёт минутами и ждёт провайдера."""
        assert JOB_TIMEOUT >= 1800


class TestHealth:
    async def test_alive_when_database_answers(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "жив"}

    async def test_no_pass_needed(self, client: AsyncClient) -> None:
        """Проверку живости делает контейнер, у которого учётки нет."""
        response = await client.get("/api/health", headers={})

        assert response.status_code == 200

    async def test_dead_database_is_not_alive(
        self, client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сервер, отвечающий «жив» с недоступной базой, заставляет контейнер
        принимать запросы, каждый из которых падает."""

        async def refuse(*_: object, **__: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("база не отвечает"))

        monkeypatch.setattr(session, "execute", refuse)

        response = await client.get("/api/health")

        assert response.status_code == 503
        assert "база" in response.json()["status"]


class TestSandboxDoesNotSpend:
    """Песочница выдачи и живой ключ Ahrefs вместе — это трата на выдуманные
    домены. Стоило 670 юнитов 19.09.2026: задача из очереди прошла с таким
    сочетанием, и заметить это можно было только по счёту."""

    def test_sandbox_with_live_key_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.config.serp.SANDBOX", True)
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "живой-ключ")
        monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")

        with pytest.raises(ConfigError, match=r"[Пп]есочниц"):
            check_collect()

    def test_refusal_says_what_to_do(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.config.serp.SANDBOX", True)
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "живой-ключ")
        monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")

        with pytest.raises(ConfigError) as failure:
            check_collect()

        assert "AHREFS_API_KEY" in str(failure.value)
        assert "SERP_SANDBOX" in str(failure.value)

    def test_live_run_still_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Боевой прогон не задет: запрет ровно на сочетание."""
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "живой-ключ")
        monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")

        check_collect()

    def test_worker_job_checks_config_like_the_command(self) -> None:
        """Задача из очереди обязана проверять то же, что консольная команда:
        мимо неё идёт тот же прогон и те же деньги."""
        source = Path(jobs.__file__ or "").read_text(encoding="utf-8")
        assert "check_collect()" in source
        assert "check_storage()" in source
