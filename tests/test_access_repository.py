"""Учётки, вход и журнал — на настоящей базе.

Здесь проверяется то, что на подделке проверить нельзя: уникальность
почты держит база, а не код; запись журнала обязана лежать в той же
транзакции, что и действие.
"""

from __future__ import annotations

import pytest
from backend.features.access.login import LoginFailedError, login
from backend.features.access.permissions import has_permission
from backend.features.access.repository import (
    AccessRepository,
    EmailTakenError,
    actor_of,
)
from backend.features.core.domain import AuditAction, Permission, UserRole
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

PASSWORD = "разовый-пароль-1234"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "x" * 64)


async def _admin(session: AsyncSession, email: str = "admin@site.com") -> int:
    user = await AccessRepository(session).create(
        email=email, password=PASSWORD, role=UserRole.ADMIN
    )
    return int(user.id)


class TestAccounts:
    async def test_created_account_can_log_in(self, session: AsyncSession) -> None:
        await _admin(session)
        result = await login(session, "admin@site.com", PASSWORD)

        assert result.email == "admin@site.com"
        assert result.role is UserRole.ADMIN
        assert result.must_change_password  # пароль выдан разовый

    async def test_email_is_stored_lowercase(self, session: AsyncSession) -> None:
        """Иначе `Ivan@site.com` и `ivan@site.com` — двое, хотя человек один."""
        await AccessRepository(session).create(
            email="  Ivan@Site.COM ", password=PASSWORD, role=UserRole.OPERATOR
        )
        result = await login(session, "IVAN@site.com", PASSWORD)
        assert result.email == "ivan@site.com"

    async def test_second_account_with_same_email_refused(self, session: AsyncSession) -> None:
        repository = AccessRepository(session)
        await repository.create(email="one@site.com", password=PASSWORD, role=UserRole.OPERATOR)
        with pytest.raises(EmailTakenError):
            await repository.create(email="ONE@site.com", password=PASSWORD, role=UserRole.OPERATOR)

    async def test_password_is_not_stored_as_is(self, session: AsyncSession) -> None:
        user_id = await _admin(session)
        user = await AccessRepository(session).by_id(user_id)
        assert user is not None
        assert PASSWORD not in user.password_hash


class TestLogin:
    async def test_wrong_password_refused_with_the_same_words(self, session: AsyncSession) -> None:
        """«Нет такой почты» и «неверный пароль» — одно сообщение: разные
        подтверждают подбирающему, что учётка существует."""
        await _admin(session)

        with pytest.raises(LoginFailedError) as wrong_password:
            await login(session, "admin@site.com", "не тот")
        with pytest.raises(LoginFailedError) as no_account:
            await login(session, "нет-такого@site.com", PASSWORD)

        assert str(wrong_password.value) == str(no_account.value)

    async def test_disabled_account_does_not_log_in(self, session: AsyncSession) -> None:
        user_id = await _admin(session)
        await AccessRepository(session).update_access(user_id, is_active=False)

        with pytest.raises(LoginFailedError):
            await login(session, "admin@site.com", PASSWORD)

    async def test_login_is_noted(self, session: AsyncSession) -> None:
        user_id = await _admin(session)
        await login(session, "admin@site.com", PASSWORD)
        await session.flush()

        user = await AccessRepository(session).by_id(user_id)
        assert user is not None
        assert user.last_login_at is not None


class TestJournal:
    async def test_actions_land_in_the_journal(self, session: AsyncSession) -> None:
        user_id = await _admin(session)
        await login(session, "admin@site.com", PASSWORD)
        await session.flush()

        entries = await AccessRepository(session).journal()
        actions = {entry.action for entry in entries}

        assert AuditAction.USER_CREATED in actions
        assert AuditAction.LOGIN in actions
        assert any(entry.target == f"user:{user_id}" for entry in entries)

    async def test_failed_login_is_visible(self, session: AsyncSession) -> None:
        """Иначе подбор пароля выглядит как тишина."""
        await _admin(session)
        with pytest.raises(LoginFailedError):
            await login(session, "admin@site.com", "не тот")
        await session.flush()

        entries = await AccessRepository(session).journal()
        failed = [e for e in entries if e.action is AuditAction.LOGIN_FAILED]
        assert failed
        assert failed[0].details is not None
        assert failed[0].details["email"] == "admin@site.com"

    async def test_unknown_email_is_recorded_without_an_author(self, session: AsyncSession) -> None:
        with pytest.raises(LoginFailedError):
            await login(session, "чужой@site.com", "любой")
        await session.flush()

        entries = await AccessRepository(session).journal()
        assert entries[0].user_id is None
        assert entries[0].details is not None
        assert entries[0].details["email"] == "чужой@site.com"

    async def test_rights_change_is_recorded_with_its_author(self, session: AsyncSession) -> None:
        admin_id = await _admin(session)
        operator = await AccessRepository(session).create(
            email="operator@site.com", password=PASSWORD, role=UserRole.OPERATOR
        )

        await AccessRepository(session).update_access(
            operator.id, permissions={"send": True}, author_id=admin_id
        )
        await session.flush()

        entries = await AccessRepository(session).journal()
        change = next(e for e in entries if e.action is AuditAction.USER_UPDATED)
        assert change.user_id == admin_id
        assert change.target == f"user:{operator.id}"


class TestRightsFromDatabase:
    async def test_granted_permission_works_after_reload(self, session: AsyncSession) -> None:
        """Точечное право живёт в базе, а не в пропуске: выдали — действует
        сразу, отобрали — перестаёт, не дожидаясь конца суток."""
        repository = AccessRepository(session)
        user = await repository.create(
            email="sender@site.com", password=PASSWORD, role=UserRole.OPERATOR
        )
        assert not has_permission(actor_of(user), Permission.SEND)

        await repository.update_access(user.id, permissions={"send": True})
        await session.flush()
        await session.refresh(user)

        assert has_permission(actor_of(user), Permission.SEND)
