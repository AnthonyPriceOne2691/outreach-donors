"""Правила доступа без сервера: счётчик попыток и управление учётками.

Сюда вынесено то, что должно проверяться без HTTP: правило, которое
живёт только в обработчике запроса, из быстрых тестов исчезает.
Время в счётчике попыток подаётся снаружи — тест на подборе пароля,
который ждёт минуту, не запускают.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.access.administration import (
    LastAdminError,
    SelfLockoutError,
    UnknownUserError,
    change_own_password,
    create_user,
    reset_password,
    update_access,
)
from backend.features.access.attempts import LoginAttempts, TooManyAttemptsError
from backend.features.access.passwords import verify_password
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from sqlalchemy.ext.asyncio import AsyncSession

MakeUser = Callable[..., Awaitable[UserModel]]
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class TestLoginAttempts:
    def test_limit_is_reached_and_then_refuses(self) -> None:
        counter = LoginAttempts(limit=3)
        for _ in range(3):
            counter.failed("email:ivan@site.com", now=NOW)

        with pytest.raises(TooManyAttemptsError, match="Повторите через"):
            counter.check("email:ivan@site.com", now=NOW)

    def test_window_moves(self) -> None:
        """Окно скользящее: минуту спустя старые попытки не считаются."""
        counter = LoginAttempts(limit=3)
        for _ in range(3):
            counter.failed("email:ivan@site.com", now=NOW)

        counter.check("email:ivan@site.com", now=NOW + timedelta(minutes=1, seconds=1))

    def test_keys_do_not_mix(self) -> None:
        counter = LoginAttempts(limit=2)
        counter.failed("email:ivan@site.com", "addr:1.2.3.4", now=NOW)
        counter.failed("email:ivan@site.com", "addr:1.2.3.4", now=NOW)

        # Перебор по чужой почте с того же адреса ловится адресом,
        # хотя у этой почты попыток ещё не было.
        with pytest.raises(TooManyAttemptsError):
            counter.check("email:другой@site.com", "addr:1.2.3.4", now=NOW)

    def test_success_clears_the_count(self) -> None:
        counter = LoginAttempts(limit=2)
        counter.failed("email:ivan@site.com", now=NOW)
        counter.succeeded("email:ivan@site.com")
        counter.failed("email:ivan@site.com", now=NOW)

        counter.check("email:ivan@site.com", now=NOW)

    def test_old_records_do_not_pile_up(self) -> None:
        """Счётчик живёт столько же, сколько процесс: без уборки словарь
        растёт на каждую новую почту и не уменьшается никогда."""
        counter = LoginAttempts(limit=5)
        for i in range(100):
            counter.failed(f"email:{i}@site.com", now=NOW)

        later = NOW + timedelta(minutes=2)
        for i in range(100):
            counter.check(f"email:{i}@site.com", now=later)

        assert counter._failures == {}


class TestAdministration:
    async def test_created_user_gets_a_password_that_works(self, session: AsyncSession) -> None:
        admin = await _admin(session)

        user, password = await create_user(
            session, email="новичок@site.com", role=UserRole.OPERATOR, author=actor_of(admin)
        )

        assert verify_password(password, user.password_hash)
        assert user.must_change_password is True

    async def test_one_time_passwords_differ(self, session: AsyncSession) -> None:
        admin = await _admin(session)
        _, first = await create_user(
            session, email="первый@site.com", role=UserRole.OPERATOR, author=actor_of(admin)
        )
        _, second = await create_user(
            session, email="второй@site.com", role=UserRole.OPERATOR, author=actor_of(admin)
        )
        assert first != second

    async def test_reset_requires_a_known_user(self, session: AsyncSession) -> None:
        admin = await _admin(session)
        with pytest.raises(UnknownUserError, match="9999"):
            await reset_password(session, user_id=9999, author=actor_of(admin))

    async def test_disabled_admin_does_not_count_as_a_spare(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Отключённый админ выглядит как запасной, но войти не может —
        и если считать его, сервис останется без единого действующего."""
        admin = await _admin(session)
        second = await make_user("запасной@site.com", role=UserRole.ADMIN)
        await update_access(session, user_id=second.id, author=actor_of(admin), is_active=False)

        with pytest.raises(LastAdminError):
            await update_access(
                session, user_id=admin.id, author=actor_of(second), role=UserRole.OPERATOR
            )

    async def test_demoting_an_operator_is_never_blocked(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        admin = await _admin(session)
        operator = await make_user("оператор@site.com", role=UserRole.OPERATOR)

        await update_access(session, user_id=operator.id, author=actor_of(admin), is_active=False)

        await session.refresh(operator)
        assert operator.is_active is False

    async def test_self_lockout_names_the_reason(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        admin = await _admin(session)
        await make_user("второй@site.com", role=UserRole.ADMIN)

        with pytest.raises(SelfLockoutError, match="самого себя"):
            await update_access(
                session, user_id=admin.id, author=actor_of(admin), role=UserRole.OPERATOR
            )

    async def test_own_password_change_clears_the_one_time_flag(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user("ivan@site.com", must_change_password=True)

        await change_own_password(
            session,
            user_id=user.id,
            current="пароль-для-теста",
            new="три слова подряд длиннее",
        )

        await session.refresh(user)
        assert user.must_change_password is False
        assert verify_password("три слова подряд длиннее", user.password_hash)


async def _admin(session: AsyncSession) -> UserModel:
    repository = AccessRepository(session)
    user = await repository.create(
        email="админ@site.com", password="пароль-для-теста", role=UserRole.ADMIN
    )
    await session.commit()
    return user
