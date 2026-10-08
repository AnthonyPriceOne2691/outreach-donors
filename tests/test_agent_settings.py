"""Настройки агента переписки: версии по этапам, экран и право их править.

Решение Anthony 04.10.2026: агент пишет черновики по настройкам с экрана.
Проверяется то, чего не видно по зелёному прогону: настройки у этапов свои,
правка заводит версию, а не переписывает, пока версий нет — агент выключен,
и две правки разом не ложатся друг на друга молча.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.agent import settings as agent
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import AuditAction, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

BODY = {
    "enabled": True,
    "goal": "  Узнать цену статьи со ссылкой  ",
    "tone": "Коротко",
    "points": ["Спросить цену", "", "  Попросить скидку  "],
    "price_limit_usd": "150.00",
    "stop_topics": ["Договор"],
}


class TestVersions:
    async def test_not_configured_means_no_version(self, session: AsyncSession) -> None:
        """Пока версий нет, агент не пишет: умолчания — для экрана, не для него."""
        assert await AgentSettingsRepository(session).current(Stage.DONORS) is None

    async def test_each_save_is_a_new_version_of_its_stage(self, session: AsyncSession) -> None:
        repository = AgentSettingsRepository(session)
        first = await repository.save(Stage.DONORS, agent.defaults(Stage.DONORS), author="a@x")
        second = await repository.save(Stage.DONORS, agent.defaults(Stage.DONORS), author="b@x")
        other = await repository.save(
            Stage.ADVERTISERS, agent.defaults(Stage.ADVERTISERS), author="a@x"
        )

        assert (first.version, second.version, other.version) == (1, 2, 1)
        current = await repository.current(Stage.DONORS)
        assert current is not None
        assert current.id == second.id
        assert [row.version for row in await repository.history(Stage.DONORS)] == [2, 1]

    async def test_stale_second_save_is_refused_not_merged(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Вторая правка, начатая с той же версии, что и первая, получила бы её
        номер: отказ со словами, а не тихая подмена чужой правки."""
        repository = AgentSettingsRepository(session)
        await repository.save(Stage.DONORS, agent.defaults(Stage.DONORS), author="a@x")

        async def stale(_stage: Stage) -> None:
            return None

        monkeypatch.setattr(repository, "current", stale)
        with pytest.raises(agent.AgentSettingsConflictError, match="обновите страницу"):
            await repository.save(Stage.DONORS, agent.defaults(Stage.DONORS), author="b@x")

    def test_stages_start_from_their_own_goal(self) -> None:
        """Доноров мы покупаем, рекламодателям продаём — цели противоположные."""
        donors, advertisers = agent.defaults(Stage.DONORS), agent.defaults(Stage.ADVERTISERS)
        assert donors.goal != advertisers.goal
        assert donors.price_limit_usd is None  # цену без предела агент не обещает


class TestScreen:
    async def test_everyone_with_access_sees_both_stages(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("смотрит@site.com", role=UserRole.OPERATOR)
        token = await sign_in("смотрит@site.com")

        shown = await client.get("/api/agent/settings", headers=bearer(token))

        assert shown.status_code == 200, shown.text
        stages = shown.json()["stages"]
        assert [stage["stage"] for stage in stages] == ["donors", "advertisers"]
        assert all(stage["current"] is None for stage in stages)
        assert stages[0]["defaults"]["enabled"] is True

    async def test_save_cleans_lines_and_is_journaled(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
    ) -> None:
        await make_user("правит@site.com", role=UserRole.OPERATOR)
        token = await sign_in("правит@site.com")

        saved = await client.post("/api/agent/settings/donors", json=BODY, headers=bearer(token))
        shown = await client.get("/api/agent/settings", headers=bearer(token))

        assert saved.status_code == 200, saved.text
        version = saved.json()
        assert version["version"] == 1
        assert version["created_by"] == "правит@site.com"
        assert version["settings"]["goal"] == "Узнать цену статьи со ссылкой"
        assert version["settings"]["points"] == ["Спросить цену", "Попросить скидку"]
        assert Decimal(version["settings"]["price_limit_usd"]) == Decimal("150")
        donors, advertisers = shown.json()["stages"]
        assert donors["current"]["version"] == 1
        assert advertisers["current"] is None  # этапы не делят настройки
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.AGENT_SETTINGS_CHANGED)
        )
        assert entry is not None
        assert entry.details is not None
        assert entry.details["этап"] == "donors"

    async def test_save_needs_the_settings_right(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("смотрит@site.com", role=UserRole.OPERATOR, permissions={"settings": False})
        token = await sign_in("смотрит@site.com")

        refused = await client.post("/api/agent/settings/donors", json=BODY, headers=bearer(token))

        assert refused.status_code == 403

    @pytest.mark.parametrize(
        "change",
        [
            {"goal": "   "},
            {"price_limit_usd": "1000000"},
            {"price_limit_usd": "-1"},
            {"points": ["пункт"] * 21},
        ],
    )
    async def test_impossible_settings_are_refused(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        change: dict[str, object],
    ) -> None:
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")

        refused = await client.post(
            "/api/agent/settings/donors", json={**BODY, **change}, headers=bearer(token)
        )

        assert refused.status_code == 422


def _migration(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "backend/migrations/versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(connection: Connection) -> tuple[bool, bool]:
    """Откат настроек и снова вперёд. Черновики ссылаются на версию настроек,
    а журнал сообщений продаж — на черновик: они откатываются первыми — как
    откатил бы alembic по цепочке."""

    def exists() -> bool:
        found = connection.execute(text("SELECT to_regclass('agent_settings')")).scalar()
        return found is not None

    settings = _migration("d7da62eb8165_agent_settings.py")
    drafts = _migration("7ccbaf6d840a_agent_drafts.py")
    notices = _migration("d64e2cd71614_sales_draft_notices.py")
    with Operations.context(MigrationContext.configure(connection)):
        notices.downgrade()
        drafts.downgrade()
        settings.downgrade()
        down = exists()
        settings.upgrade()
        drafts.upgrade()
        notices.upgrade()
    return down, exists()


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_tables) == (False, True)
