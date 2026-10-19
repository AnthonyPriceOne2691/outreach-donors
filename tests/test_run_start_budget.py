"""Запуск прогона доноров сверяет бюджет той же сметой, что видел человек (19.10.2026).

До этого кнопку блокировал только экран: запуск мимо экрана вставал в очередь и отказывал
уже после покупки выдачи. Остаток Ahrefs — из снимка ключа последней сметы; снимок старше
`routes.SNAPSHOT_FRESH` — сверяет задача, живым ответом Ahrefs. Слова отказа — те же, что у
задачи (`budget.shortage_words`): какой лимит поднять.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.api.runs import routes
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.run import RunModel
from backend.features.runs.budget import LIMIT_WORDS, shortage_words
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key
from tests.test_api_runs_donors import RUN_BODY, FakeQueue

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

TRIM = "Или сократите список ключей или глубину выдачи."


def _refusal(words: str) -> str:
    """Отказ запуска — слова задачи предложением: заголовок «Прогон не запущен» ставит экран."""
    return f"{words[:1].upper()}{words[1:]}. {TRIM}"


@pytest.fixture
async def runner(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("запуск@site.com", role=UserRole.OPERATOR)
    return await sign_in("запуск@site.com")


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    """Настройки — тестом, а не окружением машины; очередь — подделкой: в ней видно, ушёл ли
    прогон в работу."""
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
    return fake


class TestWords:
    @pytest.mark.parametrize(
        ("short_of", "side", "said"),
        [
            (["provider"], "key", LIMIT_WORDS["key"]),
            (["provider"], "workspace", LIMIT_WORDS["workspace"]),
            (["cap"], "key", LIMIT_WORDS["cap"]),
            (["ceiling"], "key", LIMIT_WORDS["cap"]),
            (["provider", "cap"], "key", LIMIT_WORDS["both"]),
            (["provider", "ceiling"], "workspace", LIMIT_WORDS["both"]),
        ],
    )
    def test_each_shortage_speaks_the_words_of_the_job(
        self, short_of: list[str], side: str, said: str
    ) -> None:
        assert shortage_words(short_of, side) == said

    def test_nothing_short_is_no_words(self) -> None:
        assert shortage_words([], "key") is None


class TestStart:
    async def _start(self, client: AsyncClient, token: str, **extra: object) -> tuple[int, str]:
        response = await client.post("/api/runs", json={**RUN_BODY, **extra}, headers=bearer(token))
        said = response.json().get("detail") if response.status_code == 409 else response.text
        return response.status_code, str(said)

    async def test_over_the_key_is_refused_before_the_run_row(
        self, client: AsyncClient, session: AsyncSession, runner: str, queue: FakeQueue
    ) -> None:
        await known_key(session, key_limit=10_000, key_used=9_900)

        status, said = await self._start(client, runner)

        assert status == 409, said
        assert said == _refusal(LIMIT_WORDS["key"])
        assert queue.calls == []
        assert await session.scalar(select(func.count(RunModel.id))) == 0

    async def test_short_of_the_key_and_the_ceiling_asks_to_check_both(
        self, client: AsyncClient, session: AsyncSession, runner: str, queue: FakeQueue
    ) -> None:
        await known_key(session, key_limit=10_000, key_used=9_900)

        status, said = await self._start(client, runner, cap=50)

        assert status == 409, said
        assert said == _refusal(LIMIT_WORDS["both"])
        assert queue.calls == []

    async def test_a_run_that_fits_starts(
        self, client: AsyncClient, session: AsyncSession, runner: str, queue: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)

        status, said = await self._start(client, runner)

        assert status == 200, said
        assert len(queue.calls) == 1

    async def test_an_old_snapshot_leaves_the_budget_to_the_job(
        self, client: AsyncClient, session: AsyncSession, runner: str, queue: FakeQueue
    ) -> None:
        """Снимок старше `SNAPSHOT_FRESH` о ключе уже не говорит: лимит могли поднять в Ahrefs.
        Отказ по нему не дал бы запустить то, что поместится, — сверит задача."""
        old = datetime.now(UTC) - routes.SNAPSHOT_FRESH - timedelta(minutes=1)
        await known_key(session, key_limit=10_000, key_used=9_900, asked_at=old)

        status, said = await self._start(client, runner)

        assert status == 200, said
        assert len(queue.calls) == 1
