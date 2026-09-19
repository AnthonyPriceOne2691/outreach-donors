"""Учётки и журнал: чтение, заведение, правка.

Правило, ради которого этот модуль существует отдельно: **запись
в журнал идёт в той же транзакции, что и само действие**. Журнал,
который пишется отдельно, врёт именно тогда, когда нужен: при сбое
на середине остаётся либо действие без следа, либо след без действия.
Поэтому здесь нет ни одного коммита — транзакцией управляет вызывающий.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.passwords import hash_password
from backend.features.access.permissions import Actor
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel


class EmailTakenError(ValueError):
    """Учётка с такой почтой уже есть. Заводить вторую нельзя: один
    человек в журнале должен быть одной строкой."""


def normalize_email(email: str) -> str:
    """Почта хранится в нижнем регистре и без краевых пробелов.

    Иначе `Ivan@site.com` и `ivan@site.com` заводятся как двое, а в журнале
    это два разных человека, хотя человек один.
    """
    return (email or "").strip().lower()


def actor_of(user: UserModel) -> Actor:
    """Модель базы → то, с чем работает проверка прав."""
    return Actor(
        user_id=user.id,
        role=user.role,
        is_active=user.is_active,
        overrides=user.permissions,
    )


class AccessRepository:
    """Доступ к учёткам и журналу."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- чтение ---

    async def by_email(self, email: str) -> UserModel | None:
        rows = await self._session.execute(
            select(UserModel).where(UserModel.email == normalize_email(email))
        )
        return rows.scalar_one_or_none()

    async def by_id(self, user_id: int) -> UserModel | None:
        return await self._session.get(UserModel, user_id)

    async def all_users(self) -> Sequence[UserModel]:
        rows = await self._session.execute(select(UserModel).order_by(UserModel.email))
        return rows.scalars().all()

    async def count(self) -> int:
        rows = await self._session.execute(select(UserModel.id))
        return len(rows.scalars().all())

    async def count_active_admins(self) -> int:
        """Сколько людей сейчас могут завести учётку и выдать права.

        Число нужно ровно в одном месте — перед тем как отобрать роль
        у админа. Ноль здесь означает сервис, в который никто не может
        впустить нового человека.
        """
        rows = await self._session.execute(
            select(UserModel.id).where(
                UserModel.role == UserRole.ADMIN, UserModel.is_active.is_(True)
            )
        )
        return len(rows.scalars().all())

    # --- изменение ---

    async def create(
        self,
        *,
        email: str,
        password: str,
        role: UserRole,
        author_id: int | None = None,
    ) -> UserModel:
        """Завести учётку. Пароль приходит уже сгенерированным: показать
        его один раз — забота вызывающего, хранить его негде."""
        user = UserModel(
            email=normalize_email(email),
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            must_change_password=True,
        )
        # Вставка идёт во вложенной транзакции: отказ уникальности иначе
        # отравляет всю сессию, и вызывающий не может ни продолжить работу,
        # ни даже аккуратно закрыться. С точкой сохранения откатывается
        # только неудачная вставка.
        try:
            async with self._session.begin_nested():
                self._session.add(user)
                await self._session.flush()
        except IntegrityError as exc:
            raise EmailTakenError(f"Учётка {normalize_email(email)} уже существует") from exc

        await self.record(
            AuditAction.USER_CREATED,
            author_id=author_id,
            target=f"user:{user.id}",
            details={"email": user.email, "role": role.value},
        )
        return user

    async def set_password(
        self,
        user_id: int,
        password: str,
        *,
        one_time: bool,
        author_id: int | None = None,
    ) -> None:
        """Сменить пароль. `one_time` — пароль выдан админом, и человек
        обязан сменить его при первом входе.

        Автор по умолчанию — сам владелец учётки. При сбросе админом автор
        другой, и это обязано быть видно: «сотрудник сменил себе пароль»
        и «админ выдал сотруднику новый» — разные события, и второе
        интересно ровно тогда, когда разбираются с доступом.
        """
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(password_hash=hash_password(password), must_change_password=one_time)
        )
        await self.record(
            AuditAction.PASSWORD_CHANGED,
            author_id=author_id if author_id is not None else user_id,
            target=f"user:{user_id}",
        )

    async def update_access(
        self,
        user_id: int,
        *,
        role: UserRole | None = None,
        is_active: bool | None = None,
        permissions: dict[str, Any] | None = None,
        author_id: int | None = None,
    ) -> None:
        """Роль, активность и точечные права. Удаления учётки нет: вместе
        с ней ушла бы история её действий."""
        values: dict[str, Any] = {}
        if role is not None:
            values["role"] = role
        if is_active is not None:
            values["is_active"] = is_active
        if permissions is not None:
            values["permissions"] = permissions
        if not values:
            return

        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(**values)
        )
        await self.record(
            AuditAction.USER_UPDATED,
            author_id=author_id,
            target=f"user:{user_id}",
            details={k: (v.value if isinstance(v, UserRole) else v) for k, v in values.items()},
        )

    async def note_login(self, user_id: int, *, now: datetime | None = None) -> None:
        await self._session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(last_login_at=now or datetime.now(UTC))
        )
        await self.record(AuditAction.LOGIN, author_id=user_id, target=f"user:{user_id}")

    # --- журнал ---

    async def record(
        self,
        action: AuditAction,
        *,
        author_id: int | None = None,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие. Без коммита: запись обязана попасть в ту же
        транзакцию, что и само действие."""
        self._session.add(
            AuditLogModel(
                user_id=author_id,
                action=action,
                target=target,
                details=details,
            )
        )

    async def journal(self, *, limit: int = 100) -> Sequence[AuditLogModel]:
        rows = await self._session.execute(
            select(AuditLogModel).order_by(AuditLogModel.created_at.desc()).limit(limit)
        )
        return rows.scalars().all()
