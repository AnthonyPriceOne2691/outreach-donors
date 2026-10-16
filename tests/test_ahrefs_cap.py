"""Месячный кап юнитов Ahrefs — на экране «Расход», меняет учётка с правом «править пороги».

Решение владельца 16.10.2026: кап виден на экране «Расход», и тот, у кого право править
настройки, меняет его сам; видно, кто и когда менял. До этого кап жил только в переменной
`AHREFS_UNITS_CAP`, и смета советовала «поднимите кап» тому, у кого поднять его было нечем.

Проверяется: без смен действует окружение; смену видят все, кто читает кап, — смета,
потолок прогона на нажатии и на старте задачи, пакет DR Этапа 2, тревога сторожа, «Обзор»
и экран «Расход»; границы отказывают словами; без права — 403 и ничего не записано; смена
пишется в журнал «было → стало»; две смены разом идут по очереди; ревизия ходит вниз и вверх.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.runs.routes import effective_cap
from backend.api.settings import routes as settings_routes
from backend.features.access.permissions import no_right
from backend.features.access.repository import AccessRepository
from backend.features.core import usage
from backend.features.core.domain import AuditAction, Permission, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import AhrefsCapModel
from backend.features.ops.overview import overview
from backend.features.ops.silence import alarms
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.ahrefs_cap import (
    CapRangeError,
    ahrefs_units_cap,
    cap_changes,
    set_ahrefs_cap,
)
from backend.features.runs.budget import ceiling_at_start, units_free
from backend.features.runs.spending import cap_left, month_cap
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

MIGRATION = "f0ec1a11f480_ahrefs_caps.py"
#: Умолчание окружения и лимит тарифа в тестах — круглые и не совпадающие ни с одной сменой.
ENV_CAP = 100_000
TARIFF = 1_000_000
#: Потрачено нами с начала месяца.
SPENT = 6_000
NBSP = " "
CAP_URL = "/api/usage/ahrefs-cap"


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.config.ahrefs.UNITS_CAP", ENV_CAP)
    monkeypatch.setattr("backend.config.ahrefs.MONTHLY_UNITS", TARIFF)


@pytest.fixture
def quiet_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остатки у провайдеров не спрашиваются: экран расхода — о своей таблице и капе."""

    class _Silent:
        async def limits_and_usage(self) -> None:
            raise OSError("тест: остаток не спрашиваем")

        async def aclose(self) -> None:
            return None

    async def no_serp() -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(settings_routes, "AhrefsClient", _Silent)
    monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user("кап@team.example.test", role=UserRole.ADMIN)


@pytest.fixture
async def admin_token(admin: UserModel, sign_in: SignIn) -> str:
    return await sign_in(admin.email)


async def _spent(session: AsyncSession, units: int = SPENT) -> None:
    usage.record(session, operation="batch_metrics", units=units, stage=Stage.DONORS)
    await session.flush()


class _Plenty:
    """Ahrefs, у которого юнитов вдоволь: ограничивает только наш кап."""

    async def limits_and_usage(self) -> dict[str, int]:
        return {
            "units_limit_workspace": 10_000_000,
            "units_usage_workspace": 0,
            "units_limit_api_key": 10_000_000,
            "units_usage_api_key": 0,
        }

    async def aclose(self) -> None:
        return None


class TestWithoutChanges:
    async def test_the_environment_is_the_cap(self, session: AsyncSession) -> None:
        await _spent(session)

        assert await ahrefs_units_cap(session) == ENV_CAP
        assert await cap_changes(session) == []
        assert await cap_left(session) == ENV_CAP - SPENT

    @pytest.mark.usefixtures("quiet_providers")
    async def test_the_screen_names_the_default_and_its_limits(
        self, client: AsyncClient, session: AsyncSession, admin_token: str
    ) -> None:
        await _spent(session)

        body = (await client.get("/api/usage", headers=bearer(admin_token))).json()

        assert body["ahrefs_cap"] == ENV_CAP
        assert body["ahrefs_cap_left"] == ENV_CAP - SPENT
        assert body["ahrefs_cap_limits"] == {"min": 1, "max": TARIFF}
        assert body["ahrefs_cap_changes"] == []


class TestAfterAChange:
    """Кап с экрана берут все, кто его читает, — не только сам экран."""

    async def test_the_screen_shows_the_cap_who_and_when(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        quiet_providers: None,
    ) -> None:
        await _spent(session)

        saved = await client.post(CAP_URL, json={"units_cap": 150_000}, headers=bearer(admin_token))
        body = (await client.get("/api/usage", headers=bearer(admin_token))).json()

        assert saved.status_code == 200, saved.text
        assert saved.json()["units_cap"] == 150_000
        assert saved.json()["set_by"] == "кап@team.example.test"
        assert body["ahrefs_cap"] == 150_000
        assert body["ahrefs_cap_left"] == 150_000 - SPENT
        [change] = body["ahrefs_cap_changes"]
        assert (change["units_cap"], change["set_by"]) == (150_000, "кап@team.example.test")
        assert datetime.fromisoformat(change["set_at"]) > datetime.now(UTC) - timedelta(hours=1)

    async def test_newest_change_first_and_it_is_the_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        for cap in (150_000, 120_000, 200_000):
            await set_ahrefs_cap(session, cap, author=admin)

        assert [change.units_cap for change in await cap_changes(session)] == [
            200_000,
            120_000,
            150_000,
        ]
        assert await ahrefs_units_cap(session) == 200_000

    async def test_the_estimate_counts_from_the_new_cap(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def plenty(_: object) -> int:
            return 10_000_000

        monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _Plenty)
        monkeypatch.setattr("backend.api.runs.routes.units_left", plenty)
        await _spent(session)
        await set_ahrefs_cap(session, 150_000, author=admin)

        response = await client.post(
            "/api/runs/estimate",
            json={"keywords": ["ремонт квартир"], "country": "us"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["units_cap"] == 150_000
        assert response.json()["cap_left"] == 150_000 - SPENT
        assert response.json()["units_spent_this_month"] == SPENT

    @pytest.mark.parametrize(("asked", "expected"), [(None, 4_000), (1_500, 1_500), (9_999, 4_000)])
    async def test_the_run_ceiling_on_the_click_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel, asked: int | None, expected: int
    ) -> None:
        """Опущенный кап: потолок прогона на нажатии — остаток по нему, а не по окружению."""
        await _spent(session, 1_000)
        await set_ahrefs_cap(session, 5_000, author=admin)

        assert await effective_cap(session, asked=asked) == expected

    async def test_the_run_ceiling_at_start_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Кап опустили между нажатием и стартом задачи — обещанное при нажатии режется."""
        await _spent(session, 1_000)
        await set_ahrefs_cap(session, 5_000, author=admin)

        assert await ceiling_at_start(session, run_id=0, promised=50_000) == 4_000

    async def test_stage_two_dr_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _spent(session)
        await set_ahrefs_cap(session, 7_000, author=admin)

        assert await units_free(session, _Plenty()) == 1_000  # type: ignore[arg-type]

    async def test_the_watchdog_follows_the_cap_and_names_the_screen(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Кап выбран — тревога словами и с экраном, где его поднять; подняли — тревоги нет."""
        await _spent(session, 120_000)

        before = {alarm.code: alarm for alarm in await alarms(session, stages=EVERY_STAGE)}
        await set_ahrefs_cap(session, 150_000, author=admin)
        after = {alarm.code for alarm in await alarms(session, stages=EVERY_STAGE)}

        detail = before["cap-reached"].detail
        assert f"Потрачено 120{NBSP}000 юнитов Ahrefs при капе 100{NBSP}000" in detail
        assert "на экране «Расход»" in detail
        assert "cap-reached" not in after

    async def test_the_overview_shows_the_new_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await set_ahrefs_cap(session, 150_000, author=admin)

        assert (await overview(session, stages=EVERY_STAGE)).ahrefs_cap == 150_000

    async def test_the_month_reads_cap_and_spent_together(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _spent(session, 160_000)
        await set_ahrefs_cap(session, 150_000, author=admin)

        month = await month_cap(session)

        assert (month.units_cap, month.spent, month.left) == (150_000, 160_000, 0)


class TestBounds:
    """От единицы до лимита тарифа; за границей — отказ словами, и ничего не записано."""

    @pytest.mark.parametrize("cap", [1, TARIFF])
    async def test_the_edges_themselves_are_taken(
        self, client: AsyncClient, admin_token: str, cap: int
    ) -> None:
        response = await client.post(CAP_URL, json={"units_cap": cap}, headers=bearer(admin_token))

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        ("cap", "why"),
        [
            (0, "кап меньше единицы"),
            (-5, "кап меньше единицы"),
            (TARIFF + 1, "выше лимита тарифа Ahrefs кап ничего не ограничивает"),
        ],
    )
    async def test_outside_is_refused_in_words(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        cap: int,
        why: str,
    ) -> None:
        response = await client.post(CAP_URL, json={"units_cap": cap}, headers=bearer(admin_token))

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert f"допустимо от 1 до 1{NBSP}000{NBSP}000 юнитов в месяц" in detail
        assert why in detail
        assert await session.scalar(select(func.count()).select_from(AhrefsCapModel)) == 0

    @pytest.mark.parametrize("sent", [1.5, "150000", True, None])
    async def test_not_a_whole_number_is_refused_in_words(
        self, client: AsyncClient, admin_token: str, sent: object
    ) -> None:
        response = await client.post(CAP_URL, json={"units_cap": sent}, headers=bearer(admin_token))

        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["msg"] == (
            "Кап — целое число юнитов в месяц, например 150 000"
        )

    async def test_the_change_itself_refuses_without_the_screen(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Правило границ — у самой смены, а не только у маршрута."""
        with pytest.raises(CapRangeError, match="допустимо от 1"):
            await set_ahrefs_cap(session, TARIFF + 1, author=admin)

        assert await cap_changes(session) == []


class TestTheRight:
    """Под тем же правом, что пороги: «править пороги» (`settings`)."""

    async def test_without_the_right_it_is_refused_and_nothing_is_written(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        sign_in: SignIn,
        quiet_providers: None,
    ) -> None:
        await make_user("смотрит@team.example.test", permissions={"settings": False})
        token = await sign_in("смотрит@team.example.test")

        refused = await client.post(CAP_URL, json={"units_cap": 150_000}, headers=bearer(token))
        screen = await client.get("/api/usage", headers=bearer(token))

        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == no_right(Permission.SETTINGS)
        assert await session.scalar(select(func.count()).select_from(AhrefsCapModel)) == 0
        # Смотреть кап и его смены — можно: экран расхода открыт всем, кто смотрит базу.
        assert screen.status_code == 200, screen.text
        assert screen.json()["ahrefs_cap"] == ENV_CAP

    async def test_an_operator_has_the_right_by_role(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Право «править пороги» — в роли оператора (`access/permissions.ROLE_PERMISSIONS`):
        кап меняет и оператор, пока право у него не снято поимённо."""
        await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
        token = await sign_in("оператор@team.example.test")

        response = await client.post(CAP_URL, json={"units_cap": 150_000}, headers=bearer(token))

        assert response.status_code == 200, response.text

    async def test_without_a_pass_nobody(self, client: AsyncClient) -> None:
        response = await client.post(CAP_URL, json={"units_cap": 150_000})

        assert response.status_code == 403, response.text


class TestTheJournal:
    async def test_each_change_is_written_was_and_became(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, admin_token: str
    ) -> None:
        for cap in (150_000, 120_000):
            response = await client.post(
                CAP_URL, json={"units_cap": cap}, headers=bearer(admin_token)
            )
            assert response.status_code == 200, response.text

        rows = (
            await session.scalars(
                select(AuditLogModel)
                .where(AuditLogModel.action == AuditAction.AHREFS_CAP_CHANGED)
                .order_by(AuditLogModel.id)
            )
        ).all()
        ids = [change.id for change in reversed(await cap_changes(session))]

        assert [row.user_id for row in rows] == [admin.id, admin.id]
        assert [row.target for row in rows] == [f"ahrefs_cap:{number}" for number in ids]
        assert [row.details for row in rows] == [
            {"кап, юнитов в месяц": {"было": ENV_CAP, "стало": 150_000}},
            {"кап, юнитов в месяц": {"было": 150_000, "стало": 120_000}},
        ]


async def _waiting_for_a_lock(factory: Callable[[], AsyncSession]) -> bool:
    """Ждёт ли кто-нибудь рекомендательного замка в этой базе — по `pg_locks`, а не по часам.
    База — своя: `pg_locks` видит весь сервер, и замок соседнего прогона тестов не в счёт."""
    async with factory() as probe:
        waiting = await probe.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        )
    return bool(waiting)


async def test_two_changes_at_once_go_in_turn() -> None:
    """Вторая смена ждёт фиксации первой и записывает её кап как «было».

    На настоящих фиксациях: внутри одной транзакции теста замок взял бы сам себя, и
    вторая смена не ждала бы ничего. Без замка вторая прочла бы «было» до фиксации
    первой — умолчание окружения, и журнал соврал бы о второй смене.
    """
    async with committed_sessions() as factory:
        async with factory() as setup:
            author = await AccessRepository(setup).create(
                email="гонка@team.example.test", password="пароль-для-теста", role=UserRole.ADMIN
            )
            await setup.commit()

        async with factory() as first, factory() as second:
            done_first = await set_ahrefs_cap(first, 120_000, author=author)
            later = asyncio.create_task(set_ahrefs_cap(second, 150_000, author=author))
            for _ in range(100):
                if await _waiting_for_a_lock(factory):
                    break
                await asyncio.sleep(0.05)
            else:
                later.cancel()
                pytest.fail("вторая смена не ждала замка первой")
            assert not later.done()
            await first.commit()
            done_second = await asyncio.wait_for(later, timeout=10)
            await second.commit()

    assert done_first.was == ENV_CAP
    assert done_second.was == 120_000


def _down_and_up(connection: Connection) -> tuple[bool, bool, int]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его не видит.
    `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""

    def present() -> bool:
        return bool(connection.scalar(text("SELECT to_regclass('ahrefs_caps') IS NOT NULL")))

    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        migration.upgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return down, present(), list(values.scalars()).count("ahrefs_cap_changed")


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up, journal_values = await connection.run_sync(_down_and_up)

    assert (down, up, journal_values) == (False, True, 1)
