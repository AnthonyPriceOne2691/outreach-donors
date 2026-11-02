"""Роли и права одной матрицей: новая роль — строка, и ничего не забыто.

Решение 02.11.2026: другая сторона просила либо учётку со скрытыми разделами, либо два
сервиса, а вышла роль со списком прав: завели учётку, навесили роль, и всё. Правки в обработчиках
роль не требует, зато требует четырёх строк в разных местах: значения, матрицы, слов экрана и
значения в базе. Забытая строка не падает сама: роль без матрицы молча не может ничего, роль без
значения в базе падает на заведении учётки, а экран без слов показывает код. Поэтому здесь —
обходом всех ролей и прав: добавленная роль проверяется сама.

Правило этапа (`STAGE_RIGHTS`) — без базы, с двух сторон. Маршруты и данные Этапа 2 —
`tests/test_stage_two_access.py` и `tests/test_stage_two_screens.py`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.cli.main import build_parser
from backend.features.access.permissions import (
    PERMISSION_TITLES,
    ROLE_PERMISSIONS,
    STAGE_RIGHTS,
    AccessDeniedError,
    Actor,
    has_permission,
    permissions_of,
    require_stage,
    sees_stage,
    visible_stages,
)
from backend.features.core.domain import Permission, Stage, UserRole
from backend.features.core.models.access import UserModel
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

FRONT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api"

#: Роль Этапа 1: как оператор без продаж и рекламодателей, отправка — поимённо.
#: Общее у ролей этапа: смотреть базу, запускать, править, подтверждать цены.
COMMON = {Permission.VIEW, Permission.RUN, Permission.SETTINGS, Permission.PRICES}


def _keys_of(name: str) -> set[str]:
    """Ключи записи `name` из `labels.ts`."""
    source = (FRONT / "labels.ts").read_text(encoding="utf-8")
    found = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert found is not None, f"в labels.ts нет {name}"
    return set(re.findall(r"^\s*([a-z_]+):", found.group(1), re.M))


def _union_of(name: str) -> set[str]:
    """Значения объединения `name` из `types.ts`."""
    source = (FRONT / "types.ts").read_text(encoding="utf-8")
    found = re.search(rf"export type {name} =(.*?);", source, re.S)
    assert found is not None, f"в types.ts нет {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


class TestMatrix:
    @pytest.mark.parametrize("role", list(UserRole), ids=lambda r: r.value)
    def test_every_role_has_a_row_of_rights(self, role: UserRole) -> None:
        """Строки нет — `permissions_of` молча отдаёт пусто, и учётка не может ничего."""
        assert role in ROLE_PERMISSIONS
        assert Permission.VIEW in permissions_of(role)

    def test_the_outreach_role_is_stage_one_only(self) -> None:
        assert permissions_of(UserRole.DONORS) == {*COMMON, Permission.DONORS}

    def test_the_advertisers_role_is_stage_two_only(self) -> None:
        assert permissions_of(UserRole.ADVERTISERS) == {*COMMON, Permission.ADVERTISERS}

    def test_the_stage_roles_do_not_overlap_in_their_stage_rights(self) -> None:
        """Роли этапов — разные и обе отдельно от админской (слово Anthony 02.11.2026)."""
        admin_only = {
            Permission.SEND,
            Permission.SENDERS,
            Permission.USERS,
            Permission.SPEND,
            Permission.USAGE,
            Permission.SALES,
        }
        for role in (UserRole.DONORS, UserRole.ADVERTISERS):
            assert not permissions_of(role) & admin_only
        assert visible_stages(Actor(user_id=1, role=UserRole.DONORS)) == {Stage.DONORS}
        assert visible_stages(Actor(user_id=2, role=UserRole.ADVERTISERS)) == {Stage.ADVERTISERS}

    def test_the_operator_keeps_what_they_had_and_gets_the_new_right(self) -> None:
        """Нынешние учётки не теряют ничего: без этой строки операторы потеряли бы этапы и «Расход»."""
        assert permissions_of(UserRole.OPERATOR) == {
            *COMMON,
            Permission.SALES,
            Permission.ADVERTISERS,
            Permission.DONORS,
            Permission.USAGE,
        }

    def test_the_admin_has_every_right(self) -> None:
        assert permissions_of(UserRole.ADMIN) == set(Permission)

    def test_sending_is_given_by_name_as_for_an_operator(self) -> None:
        outsider = Actor(user_id=1, role=UserRole.DONORS)
        sender = Actor(user_id=2, role=UserRole.DONORS, overrides={"send": True})

        assert not has_permission(outsider, Permission.SEND)
        assert has_permission(sender, Permission.SEND)
        assert visible_stages(sender) == {Stage.DONORS}

    def test_a_right_can_be_given_to_the_role_by_name_too(self) -> None:
        """Роль — умолчание, а не потолок: право этапа выдаётся учётке поимённо."""
        wider = Actor(user_id=3, role=UserRole.DONORS, overrides={"advertisers": True})

        assert visible_stages(wider) == {Stage.DONORS, Stage.ADVERTISERS}


class TestStageRule:
    DONORS = Actor(user_id=1, role=UserRole.DONORS)
    OPERATOR = Actor(user_id=2, role=UserRole.OPERATOR)
    ADMIN_WITHOUT = Actor(user_id=3, role=UserRole.ADMIN, overrides={"advertisers": False})

    def test_every_stage_with_a_right_has_it_in_words(self) -> None:
        assert set(STAGE_RIGHTS.values()) <= set(PERMISSION_TITLES)
        assert PERMISSION_TITLES[Permission.ADVERTISERS] == "Рекламодатели"

    def test_the_operator_sees_every_stage(self) -> None:
        assert visible_stages(self.OPERATOR) == frozenset(Stage)

    def test_the_donors_role_sees_only_donors(self) -> None:
        assert visible_stages(self.DONORS) == {Stage.DONORS}

    def test_the_rule_is_about_the_right_and_not_the_role(self) -> None:
        assert not sees_stage(self.ADMIN_WITHOUT, Stage.ADVERTISERS)
        assert visible_stages(self.ADMIN_WITHOUT) == {Stage.DONORS, Stage.SALES}

    @pytest.mark.parametrize(
        ("stage", "said"),
        [
            (Stage.ADVERTISERS, "Письма рекламодателей — только с правом «Рекламодатели»"),
            (Stage.SALES, "Письма продаж — только с правом «Продажи»"),
        ],
        ids=["этап 2", "продажи"],
    )
    def test_the_refusal_names_the_stage_of_the_row_and_its_right(
        self, stage: Stage, said: str
    ) -> None:
        with pytest.raises(AccessDeniedError) as refused:
            require_stage(self.DONORS, stage, "Письма {stage}")

        assert str(refused.value) == said

    @pytest.mark.parametrize("stage", [Stage.DONORS, None], ids=["доноры", "без этапа"])
    def test_donors_and_rows_without_a_stage_pass(self, stage: Stage | None) -> None:
        require_stage(self.DONORS, stage, "Письма {stage}")


class TestTheScreenKnowsEveryRole:
    def test_every_role_has_words_and_a_place_in_the_list(self) -> None:
        roles = {role.value for role in UserRole}

        assert _keys_of("ROLE_TITLES") == roles
        assert _keys_of("ROLE_ABOUT") == roles
        assert _union_of("Role") == roles

    def test_every_right_is_a_right_of_the_screen(self) -> None:
        assert _union_of("Permission") == {permission.value for permission in Permission}

    def test_stage_rights_are_the_same_on_the_screen(self) -> None:
        """Копия правила: разойдись она — экран спрашивал бы то, в чём сервер откажет."""
        source = (FRONT / "stageRights.ts").read_text(encoding="utf-8")
        found = re.search(r"const STAGE_RIGHTS\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
        assert found is not None

        screen = dict(re.findall(r"^\s*([a-z_]+): '([a-z_]+)',", found.group(1), re.M))

        assert screen == {stage.value: right.value for stage, right in STAGE_RIGHTS.items()}


class TestStorage:
    async def test_every_role_is_in_the_database_enum(self, session: AsyncSession) -> None:
        """Роль — перечисление Postgres: без значения учётка не заводится, а модель не знает."""
        rows = await session.scalars(text("SELECT unnest(enum_range(NULL::userrole))::text"))

        assert {role.value for role in UserRole} <= set(rows)

    @pytest.mark.parametrize(
        "name",
        ["c4f7a19e2d36_user_role_donors.py", "d8a2f6c31e47_user_role_advertisers.py"],
        ids=["аутрич", "рекламодатели"],
    )
    async def test_the_migration_can_run_twice_and_step_down(
        self, session: AsyncSession, name: str
    ) -> None:
        """`ADD VALUE IF NOT EXISTS` делает повтор безвредным; значение не убирается вниз."""
        migration = load_migration(name)

        def both(connection: Connection) -> None:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()

        await (await session.connection()).run_sync(both)


class TestAccountOfTheRole:
    async def test_an_account_is_created_with_the_role_and_switched_back(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("root@team.example.test", role=UserRole.ADMIN)
        admin = bearer(await sign_in("root@team.example.test"))

        made = await client.post(
            "/api/users", json={"email": "one@team.example.test", "role": "donors"}, headers=admin
        )
        changed = await client.patch(
            f"/api/users/{made.json()['user']['id']}", json={"role": "operator"}, headers=admin
        )

        assert made.status_code == 201, made.text
        assert made.json()["user"]["role"] == "donors"
        assert made.json()["user"]["permissions"] == [
            "donors",
            "prices",
            "run",
            "settings",
            "view",
        ]
        assert changed.status_code == 200, changed.text
        assert "advertisers" in changed.json()["permissions"]

    async def test_the_person_is_told_their_rights(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("doc@team.example.test", role=UserRole.DONORS)

        me = await client.get(
            "/api/auth/me", headers=bearer(await sign_in("doc@team.example.test"))
        )

        assert me.json()["role"] == "donors"
        assert "advertisers" not in me.json()["permissions"]

    def test_the_console_offers_every_role(self) -> None:
        for role in UserRole:
            args = build_parser().parse_args(
                ["user-add", "--email", "a@b.test", "--role", role.value]
            )

            assert args.role == role.value
