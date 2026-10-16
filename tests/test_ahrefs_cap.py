"""Кап юнитов Ahrefs — от ключа, на цикл ключа; на экране «Расход» его можно только сузить.

Решения владельца 16.10.2026: кап виден на «Расходе» и меняется учёткой с правом «править
пороги», видно, кто, когда и что поставил; а сам кап — от ключа: «сервис должен владеть текущей
ситуацией по ключу, а не данными, которые зашиты жёстко». До этого кап и его граница жили
в окружении (`AHREFS_UNITS_CAP`, `AHREFS_MONTHLY_UNITS`), месяц капа был календарным, а у ключа
своё обнуление — и кап показывал почти выбранным, когда у ключа оставалась большая часть.

Проверяется: без снимка ключа кап неизвестен, и тратить не от чего — словами; с ним кап —
размер ключа (своего лимита нет — лимит пространства), и следует за ключом, когда тот двигают;
наш расход — с обнуления ключа, а месяц продаж и «Обзора» — с первого числа, как был; «не
больше N» — в границах ключа, N выше ключа — действует ключ; смену видят все, кто читает кап;
без права — 403; журнал — «было → стало»; две смены разом идут по очереди; ревизия ходит вниз
и вверх. Числа — выдуманные: лимиты настоящего ключа в публичный репозиторий не идут.
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
from backend.features.ahrefs.units import Quota
from backend.features.core import usage
from backend.features.core.domain import AuditAction, Permission, Stage, UsageProvider, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import AhrefsCapModel, UsageRecordModel
from backend.features.ops.overview import overview
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.ahrefs_cap import (
    CapRangeError,
    CapUnknownError,
    cap_changes,
    key_cap,
    set_ahrefs_cap,
)
from backend.features.runs.ahrefs_key import remember
from backend.features.runs.budget import (
    QuotaUnavailableError,
    cap_left,
    ceiling_at_start,
    units_free,
)
from backend.features.runs.spending import SpendingRepository
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

MIGRATION = "dbce021e159c_ahrefs_key_snapshot.py"
#: Лимит ключа и пространства в тестах — круглые и не совпадающие ни с одной сменой.
KEY = 50_000
WORKSPACE = 500_000
#: Потрачено нами в цикле.
SPENT = 12_000
NBSP = "\u00a0"
CAP_URL = "/api/usage/ahrefs-cap"
UNKNOWN = "Ahrefs ещё не ответил — размер ключа неизвестен"


async def known_key(
    session: AsyncSession,
    key_limit: int | None = KEY,
    *,
    key_used: int = 0,
    workspace_limit: int | None = WORKSPACE,
    workspace_used: int = 0,
    reset_at: datetime | None = None,
    asked_at: datetime | None = None,
) -> None:
    """Снимок ключа — как его кладёт ответ Ahrefs (`runs/ahrefs_key.remember`): без него
    размер ключа неизвестен, и кап считать не от чего. Других тестов помощник тоже."""
    quota = Quota(
        workspace_limit=workspace_limit,
        workspace_used=workspace_used,
        key_limit=key_limit,
        key_used=key_used,
        reset_at=reset_at,
    )
    assert await remember(session, quota, at=asked_at)
    await session.flush()


@pytest.fixture
def quiet_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остатки у провайдеров не спрашиваются: экран расхода — о своей таблице и снимке."""

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


async def _spent(session: AsyncSession, units: int = SPENT, *, at: datetime | None = None) -> None:
    row = usage.record(session, operation="batch_metrics", units=units, stage=Stage.DONORS)
    if at is not None:
        row.created_at = at
    await session.flush()


class _Plenty:
    """Ahrefs, у которого юнитов вдоволь: ограничивает только наш кап."""

    async def limits_and_usage(self) -> dict[str, int]:
        return {
            "units_limit_workspace": 10 * WORKSPACE,
            "units_usage_workspace": 0,
            "units_limit_api_key": 10 * WORKSPACE,
            "units_usage_api_key": 0,
        }

    async def aclose(self) -> None:
        return None


class TestWithoutTheKey:
    """Ahrefs ещё не ответил ни разу: размер ключа неизвестен, тратить не от чего."""

    async def test_no_cap_and_the_spending_paths_refuse_in_words(
        self, session: AsyncSession
    ) -> None:
        cap = await key_cap(session)

        assert (cap.units_cap, cap.cap_left, cap.unknown) == (None, 0, UNKNOWN)
        with pytest.raises(QuotaUnavailableError, match=UNKNOWN) as caught:
            await cap_left(session)
        # Повтор поможет: сторож спросит Ahrefs первым же проходом.
        assert not caught.value.permanent
        with pytest.raises(QuotaUnavailableError, match=UNKNOWN):
            await ceiling_at_start(session, run_id=0, promised=1_000)

    @pytest.mark.usefixtures("quiet_providers")
    async def test_the_screen_says_it_and_offers_no_bounds(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        card = (await client.get("/api/usage", headers=bearer(admin_token))).json()["ahrefs_key"]

        assert (card["cap"], card["size"], card["limits"], card["asked_at"]) == (
            None,
            None,
            None,
            None,
        )
        assert card["unknown"] == UNKNOWN
        assert card["by_key"] is False, "даты обнуления нет — цикл календарный"

    async def test_n_needs_the_key_and_the_whole_key_does_not(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        with pytest.raises(CapUnknownError, match=UNKNOWN):
            await set_ahrefs_cap(session, 5_000, author=admin)

        done = await set_ahrefs_cap(session, None, author=admin)

        assert (done.was, done.size, done.change.units_cap) == (None, None, None)

    async def test_the_api_refuses_n_in_words_and_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, admin_token: str
    ) -> None:
        response = await client.post(
            CAP_URL, json={"units_cap": 5_000}, headers=bearer(admin_token)
        )

        assert response.status_code == 503, response.text
        assert UNKNOWN in response.json()["detail"]
        assert await session.scalar(select(func.count()).select_from(AhrefsCapModel)) == 0


class TestTheCapIsTheKey:
    async def test_the_whole_key_by_default(self, session: AsyncSession) -> None:
        await known_key(session)
        await _spent(session)

        cap = await key_cap(session)

        assert (cap.units_cap, cap.chosen, cap.cap_left, cap.unknown) == (
            KEY,
            None,
            KEY - SPENT,
            None,
        )
        assert await cap_left(session) == KEY - SPENT

    async def test_a_key_without_its_own_limit_takes_the_workspace(
        self, session: AsyncSession
    ) -> None:
        """Ahrefs отдаёт null вместо лимита ключа — своего лимита нет, действует лимит
        пространства. До 16.10 null читался нулём, и ключ выглядел выбранным целиком."""
        await known_key(session, key_limit=None)

        assert (await key_cap(session)).units_cap == WORKSPACE

    async def test_the_key_moved_in_ahrefs_the_cap_follows(self, session: AsyncSession) -> None:
        first = datetime.now(UTC) - timedelta(minutes=20)
        await known_key(session, asked_at=first)
        await known_key(session, key_limit=2 * KEY, asked_at=first + timedelta(minutes=10))

        assert (await key_cap(session)).units_cap == 2 * KEY

    async def test_n_below_the_key_is_the_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await known_key(session)
        await _spent(session)
        await set_ahrefs_cap(session, 20_000, author=admin)

        cap = await key_cap(session)

        assert (cap.units_cap, cap.chosen, cap.cap_left, cap.above_key) == (
            20_000,
            20_000,
            20_000 - SPENT,
            False,
        )

    async def test_n_above_a_shrunk_key_the_key_acts_and_says_so(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        first = datetime.now(UTC) - timedelta(minutes=20)
        await known_key(session, asked_at=first)
        await set_ahrefs_cap(session, KEY, author=admin)
        await known_key(session, key_limit=KEY // 2, asked_at=first + timedelta(minutes=10))

        cap = await key_cap(session)

        assert (cap.units_cap, cap.chosen, cap.above_key) == (KEY // 2, KEY, True)


class TestTheCycleIsTheKeys:
    """Замер прода 16.10.2026: ключ обнуляется не первого числа, и расход до обнуления
    съедал кап нового цикла."""

    async def test_spent_before_the_reset_is_not_in_the_cap(self, session: AsyncSession) -> None:
        now = datetime(2026, 10, 23, 12, tzinfo=UTC)
        await known_key(session, reset_at=datetime(2026, 11, 21, tzinfo=UTC))
        await _spent(session, 30_000, at=datetime(2026, 10, 15, tzinfo=UTC))
        await _spent(session, SPENT, at=datetime(2026, 10, 22, tzinfo=UTC))

        cap = await key_cap(session, now=now)
        month = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE, now=now)

        assert (cap.cycle.start, cap.cycle.next_reset, cap.cycle.by_key) == (
            datetime(2026, 10, 21, tzinfo=UTC),
            datetime(2026, 11, 21, tzinfo=UTC),
            True,
        )
        assert (cap.spent, cap.cap_left) == (SPENT, KEY - SPENT)
        # Месяц экрана, Hunter у продаж и «Обзора» — с первого числа, как и был.
        assert month.units_by_provider[UsageProvider.AHREFS] == 30_000 + SPENT

    async def test_without_a_reset_date_the_month_is_the_calendar(
        self, session: AsyncSession
    ) -> None:
        now = datetime(2026, 10, 16, 12, tzinfo=UTC)
        await known_key(session)
        await _spent(session, 30_000, at=datetime(2026, 9, 30, tzinfo=UTC))
        await _spent(session, SPENT, at=datetime(2026, 10, 2, tzinfo=UTC))

        cap = await key_cap(session, now=now)

        assert (cap.cycle.start, cap.cycle.by_key, cap.spent) == (
            datetime(2026, 10, 1, tzinfo=UTC),
            False,
            SPENT,
        )


class TestWhatIsLeft:
    """«Осталось» — до того, во что упрёмся раньше: наш кап, ключ или пространство."""

    async def test_the_neighbour_on_the_key_is_seen_through_the_key(
        self, session: AsyncSession
    ) -> None:
        await known_key(session, key_used=40_000)
        await _spent(session)

        cap = await key_cap(session)

        assert (cap.cap_left, cap.left, cap.binding, cap.used_percent) == (
            KEY - SPENT,
            KEY - 40_000,
            "key",
            80,
        )

    async def test_our_cap_binds_when_the_key_has_room(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await known_key(session, key_used=SPENT)
        await _spent(session)
        await set_ahrefs_cap(session, 15_000, author=admin)

        cap = await key_cap(session)

        assert (cap.left, cap.binding, cap.used_percent) == (3_000, "cap", 80)

    async def test_the_workspace_binds_a_key_without_its_own_limit(
        self, session: AsyncSession
    ) -> None:
        await known_key(session, key_limit=None, workspace_used=WORKSPACE - 1_000)

        cap = await key_cap(session)

        assert (cap.left, cap.binding, cap.used_percent) == (1_000, "workspace", 99)

    async def test_percent_is_whole_and_rounded_down(self, session: AsyncSession) -> None:
        """0,29 × 100 в плавающей точке — 28,999…: доля — целыми, порог — без дробей."""
        await known_key(session, key_limit=100, key_used=29)

        assert (await key_cap(session)).used_percent == 29


class TestTheChangeIsSeenEverywhere:
    """Кап с экрана берут все, кто его читает, — не только сам экран."""

    async def test_the_screen_shows_the_key_the_cycle_and_who_changed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        quiet_providers: None,
    ) -> None:
        await known_key(session, key_used=SPENT + 3_000)
        await _spent(session)

        saved = await client.post(CAP_URL, json={"units_cap": 20_000}, headers=bearer(admin_token))
        card = (await client.get("/api/usage", headers=bearer(admin_token))).json()["ahrefs_key"]

        assert saved.status_code == 200, saved.text
        assert (saved.json()["units_cap"], saved.json()["set_by"]) == (
            20_000,
            "кап@team.example.test",
        )
        assert (card["size"], card["own_limit"], card["key_used"]) == (KEY, True, SPENT + 3_000)
        assert (card["cap"], card["chosen"], card["above_key"]) == (20_000, 20_000, False)
        assert (card["spent_by_us"], card["left"], card["binding"]) == (SPENT, 8_000, "cap")
        assert card["used_percent"] == 60
        assert card["limits"] == {"min": 1, "max": KEY}
        assert card["asked_at"] is not None
        [change] = card["changes"]
        assert (change["units_cap"], change["set_by"]) == (20_000, "кап@team.example.test")
        assert datetime.fromisoformat(change["set_at"]) > datetime.now(UTC) - timedelta(hours=1)

    async def test_the_screen_asks_ahrefs_and_keeps_the_answer(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """«Расход» спрашивает Ahrefs сам — и кап на экране от ответа этой минуты."""

        async def no_serp() -> tuple[None, None]:
            return None, None

        monkeypatch.setattr(settings_routes, "AhrefsClient", _Plenty)
        monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)

        body = (await client.get("/api/usage", headers=bearer(admin_token))).json()

        assert body["ahrefs_left"] == 10 * WORKSPACE
        assert body["ahrefs_key"]["cap"] == 10 * WORKSPACE
        assert (await key_cap(session)).units_cap == 10 * WORKSPACE

    async def test_newest_change_first_and_it_is_the_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await known_key(session)
        for cap in (20_000, None, 15_000):
            await set_ahrefs_cap(session, cap, author=admin)

        assert [change.units_cap for change in await cap_changes(session)] == [15_000, None, 20_000]
        assert (await key_cap(session)).units_cap == 15_000

    async def test_the_estimate_counts_from_the_key_and_keeps_its_answer(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Смета спрашивает Ahrefs первым: его ответ — снимок ключа, и кап сметы — от него."""
        monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _Plenty)
        await _spent(session)
        await set_ahrefs_cap(session, None, author=admin)

        response = await client.post(
            "/api/runs/estimate",
            json={"keywords": ["ремонт квартир"], "country": "us"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        made = response.json()
        assert (made["units_cap"], made["cap_left"], made["units_spent_in_cycle"]) == (
            10 * WORKSPACE,
            10 * WORKSPACE - SPENT,
            SPENT,
        )
        assert (made["cycle_by_key"], made["cap_unknown"]) == (False, None)
        assert datetime.fromisoformat(made["cycle_start"]).day == 1

    @pytest.mark.parametrize(("asked", "expected"), [(None, 4_000), (1_500, 1_500), (9_999, 4_000)])
    async def test_the_run_ceiling_on_the_click_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel, asked: int | None, expected: int
    ) -> None:
        """Опущенный кап: потолок прогона на нажатии — остаток по нему, а не по ключу."""
        await known_key(session)
        await _spent(session, 1_000)
        await set_ahrefs_cap(session, 5_000, author=admin)

        assert await effective_cap(session, asked=asked) == expected

    async def test_the_run_ceiling_at_start_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Кап опустили между нажатием и стартом задачи — обещанное при нажатии режется."""
        await known_key(session)
        await _spent(session, 1_000)
        await set_ahrefs_cap(session, 5_000, author=admin)

        assert await ceiling_at_start(session, run_id=0, promised=50_000) == 4_000

    async def test_stage_two_dr_is_under_the_new_cap(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Пакет DR Этапа 2 спрашивает Ahrefs первым — снимок ключа от его ответа, кап —
        «не больше N» с экрана."""
        await known_key(session)
        await _spent(session)
        await set_ahrefs_cap(session, SPENT + 1_000, author=admin)

        assert await units_free(session, _Plenty()) == 1_000  # type: ignore[arg-type]
        assert (await key_cap(session)).size == 10 * WORKSPACE

    async def test_the_overview_shows_the_cap_of_the_cycle(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        now = datetime(2026, 10, 23, 12, tzinfo=UTC)
        await known_key(session, reset_at=datetime(2026, 11, 21, tzinfo=UTC))
        await _spent(session, 30_000, at=datetime(2026, 10, 15, tzinfo=UTC))
        await _spent(session, SPENT, at=datetime(2026, 10, 22, tzinfo=UTC))
        await set_ahrefs_cap(session, 20_000, author=admin)

        found = await overview(session, stages=EVERY_STAGE, now=now)

        assert (found.ahrefs_units, found.ahrefs_cap, found.ahrefs_by_key) == (SPENT, 20_000, True)
        assert found.ahrefs_since == datetime(2026, 10, 21, tzinfo=UTC)


class TestBounds:
    """«Не больше N» — от единицы до размера ключа; за границей — отказ словами, и ничего не
    записано."""

    @pytest.mark.parametrize("cap", [1, KEY, None])
    async def test_the_edges_and_the_whole_key_are_taken(
        self, client: AsyncClient, session: AsyncSession, admin_token: str, cap: int | None
    ) -> None:
        await known_key(session)

        response = await client.post(CAP_URL, json={"units_cap": cap}, headers=bearer(admin_token))

        assert response.status_code == 200, response.text
        assert response.json()["units_cap"] == cap

    @pytest.mark.parametrize(
        ("cap", "why"),
        [
            (0, "кап меньше единицы"),
            (-5, "кап меньше единицы"),
            (KEY + 1, "выше лимита ключа Ahrefs кап ничего не ограничивает"),
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
        await known_key(session)

        response = await client.post(CAP_URL, json={"units_cap": cap}, headers=bearer(admin_token))

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert f"допустимо от 1 до 50{NBSP}000 юнитов за цикл ключа" in detail
        assert why in detail
        assert await session.scalar(select(func.count()).select_from(AhrefsCapModel)) == 0

    @pytest.mark.parametrize("sent", [1.5, "15000", True])
    async def test_not_a_whole_number_is_refused_in_words(
        self, client: AsyncClient, admin_token: str, sent: object
    ) -> None:
        response = await client.post(CAP_URL, json={"units_cap": sent}, headers=bearer(admin_token))

        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["msg"] == (
            f"Кап — целое число юнитов за цикл ключа, например 5{NBSP}000, или весь лимит ключа"
        )

    async def test_the_change_itself_refuses_without_the_screen(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Правило границ — у самой смены, а не только у маршрута."""
        await known_key(session)

        with pytest.raises(CapRangeError, match="допустимо от 1"):
            await set_ahrefs_cap(session, KEY + 1, author=admin)

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
        await known_key(session)
        await make_user("смотрит@team.example.test", permissions={"settings": False})
        token = await sign_in("смотрит@team.example.test")

        refused = await client.post(CAP_URL, json={"units_cap": 20_000}, headers=bearer(token))
        screen = await client.get("/api/usage", headers=bearer(token))

        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == no_right(Permission.SETTINGS)
        assert await session.scalar(select(func.count()).select_from(AhrefsCapModel)) == 0
        # Смотреть кап и его смены — можно: экран расхода открыт всем, кто смотрит базу.
        assert screen.status_code == 200, screen.text
        assert screen.json()["ahrefs_key"]["cap"] == KEY

    async def test_an_operator_has_the_right_by_role(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Право «править пороги» — в роли оператора (`access/permissions.ROLE_PERMISSIONS`):
        кап меняет и оператор, пока право у него не снято поимённо."""
        await known_key(session)
        await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
        token = await sign_in("оператор@team.example.test")

        response = await client.post(CAP_URL, json={"units_cap": 20_000}, headers=bearer(token))

        assert response.status_code == 200, response.text

    async def test_without_a_pass_nobody(self, client: AsyncClient) -> None:
        response = await client.post(CAP_URL, json={"units_cap": 20_000})

        assert response.status_code == 403, response.text


class TestTheJournal:
    async def test_each_change_is_written_was_and_became_with_the_key(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, admin_token: str
    ) -> None:
        await known_key(session)
        for cap in (20_000, None):
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
            {"кап Ahrefs": {"было": "весь лимит ключа", "стало": 20_000}, "лимит ключа": KEY},
            {"кап Ahrefs": {"было": 20_000, "стало": "весь лимит ключа"}, "лимит ключа": KEY},
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
    первой — «весь лимит ключа», и журнал соврал бы о второй смене.
    """
    async with committed_sessions() as factory:
        async with factory() as setup:
            author = await AccessRepository(setup).create(
                email="гонка@team.example.test", password="пароль-для-теста", role=UserRole.ADMIN
            )
            await known_key(setup)
            await setup.commit()

        async with factory() as first, factory() as second:
            done_first = await set_ahrefs_cap(first, 20_000, author=author)
            later = asyncio.create_task(set_ahrefs_cap(second, 15_000, author=author))
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

    assert done_first.was is None
    assert done_second.was == 20_000


def _down_and_up(connection: Connection) -> tuple[bool, bool, list[int | None], bool]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его не видит.
    Смена «весь лимит ключа» вниз не проходит — прежний код её не прочтёт, — а смена с числом
    остаётся; наверху кап смены снова может быть пустым."""

    def present() -> bool:
        return bool(
            connection.scalar(text("SELECT to_regclass('ahrefs_key_snapshot') IS NOT NULL"))
        )

    connection.execute(text("INSERT INTO ahrefs_caps (units_cap) VALUES (20000), (NULL)"))
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        kept = list(connection.scalars(text("SELECT units_cap FROM ahrefs_caps ORDER BY id")))
        migration.upgrade()
    connection.execute(text("INSERT INTO ahrefs_caps (units_cap) VALUES (NULL)"))
    nullable = connection.scalar(
        text(
            "SELECT is_nullable = 'YES' FROM information_schema.columns "
            "WHERE table_name = 'ahrefs_caps' AND column_name = 'units_cap'"
        )
    )
    return down, present(), kept, bool(nullable)


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up, kept, nullable = await connection.run_sync(_down_and_up)

    assert (down, up, kept, nullable) == (False, True, [20_000], True)


async def test_the_spent_of_the_cycle_is_ours_and_only_ahrefs(session: AsyncSession) -> None:
    """В кап идут только юниты Ahrefs — все этапы; деньги выдачи и токены — другие счета."""
    await known_key(session)
    usage.record(session, operation="batch_metrics", units=SPENT, stage=Stage.ADVERTISERS)
    usage.record(session, operation="serp_search", amount_usd=42)
    usage.record(session, operation="reply_parse", units=999)
    await session.flush()

    assert (await key_cap(session)).spent == SPENT
    assert await session.scalar(select(func.count()).select_from(UsageRecordModel)) == 3
