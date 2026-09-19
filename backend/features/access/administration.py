"""Правила управления учётками: что админу нельзя даже намеренно.

Модуль существует отдельно, чтобы эти правила проверялись без сервера.
Обработчик запроса — плохое место для них: правило, уехавшее в обработчик,
исчезает из тестов, которые гоняют ядро без сети за доли секунды.

Три правила, и все три — про то, как сервис теряется целиком:

**Последний действующий админ не отключается и не разжалуется.** Иначе
учёток в базе полный список, а завести или вернуть права некому: команда
заведения работает только на машине с базой, а доступ к ней есть не у того,
кто нажал кнопку.

**Себя не отключить и не разжаловать.** Формально это частный случай
предыдущего, но сообщение нужно другое: человек, снимающий с себя роль
по ошибке, должен прочитать про себя, а не про «последнего админа».

**Свой пароль меняется только со старым.** Украденный пропуск живёт сутки;
без этого правила он превращается в постоянный доступ — вор просто
сменит пароль на свой.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.passwords import (
    WeakPasswordError,
    assert_strong_enough,
    generate_one_time,
    verify_password,
)
from backend.features.access.permissions import Actor
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel


class LastAdminError(ValueError):
    """Изменение оставило бы сервис без действующего админа."""


class SelfLockoutError(ValueError):
    """Админ отбирает права у самого себя."""


class UnknownUserError(ValueError):
    """Учётки с таким номером нет."""


class WrongPasswordError(ValueError):
    """Старый пароль не подошёл."""


def _keeps_admin_rights(*, role: UserRole | None, is_active: bool | None) -> bool:
    """Останется ли учётка действующим админом после такого изменения."""
    return role in (None, UserRole.ADMIN) and is_active is not False


async def _guard_admin_supply(
    repository: AccessRepository,
    user: UserModel,
    *,
    role: UserRole | None,
    is_active: bool | None,
    author: Actor,
) -> None:
    """Проверки, после которых админов не станет ноль."""
    if user.role is not UserRole.ADMIN or not user.is_active:
        return
    if _keeps_admin_rights(role=role, is_active=is_active):
        return

    if author.user_id == user.id:
        raise SelfLockoutError(
            "Нельзя снять права с самого себя. Попросите другого админа — "
            "иначе выйти обратно будет некому"
        )
    if await repository.count_active_admins() <= 1:
        raise LastAdminError(
            f"{user.email} — последний действующий админ. Заведите второго, "
            "а потом снимайте права с этого"
        )


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    author: Actor,
) -> tuple[UserModel, str]:
    """Завести учётку. Разовый пароль возвращается ровно один раз:
    в базе только хеш, и показать его второй раз будет нечем."""
    password = generate_one_time()
    repository = AccessRepository(session)
    user = await repository.create(
        email=email, password=password, role=role, author_id=author.user_id
    )
    return user, password


async def reset_password(
    session: AsyncSession,
    *,
    user_id: int,
    author: Actor,
) -> tuple[UserModel, str]:
    """Выдать новый разовый пароль. Старый пропуск при этом остаётся
    действующим до конца суток — это плата за отсутствие списка выданных
    пропусков; учётку под угрозой отключают, а не сбрасывают."""
    repository = AccessRepository(session)
    user = await _known(repository, user_id)
    password = generate_one_time()
    await repository.set_password(user.id, password, one_time=True, author_id=author.user_id)
    return user, password


async def update_access(
    session: AsyncSession,
    *,
    user_id: int,
    author: Actor,
    role: UserRole | None = None,
    is_active: bool | None = None,
    permissions: dict[str, Any] | None = None,
) -> UserModel:
    """Роль, активность и точечные права — одним действием и с проверками."""
    repository = AccessRepository(session)
    user = await _known(repository, user_id)
    await _guard_admin_supply(repository, user, role=role, is_active=is_active, author=author)
    await repository.update_access(
        user.id,
        role=role,
        is_active=is_active,
        permissions=permissions,
        author_id=author.user_id,
    )
    await session.refresh(user)
    return user


async def change_own_password(
    session: AsyncSession,
    *,
    user_id: int,
    current: str,
    new: str,
) -> None:
    """Смена своего пароля. Старый обязателен, новый — длиннее минимума
    и не совпадает со старым."""
    repository = AccessRepository(session)
    user = await _known(repository, user_id)

    if not verify_password(current, user.password_hash):
        raise WrongPasswordError("Старый пароль не подошёл")
    assert_strong_enough(new)
    if verify_password(new, user.password_hash):
        raise WeakPasswordError("Новый пароль совпадает со старым")

    await repository.set_password(user.id, new, one_time=False)


async def _known(repository: AccessRepository, user_id: int) -> UserModel:
    user = await repository.by_id(user_id)
    if user is None:
        raise UnknownUserError(f"Учётки №{user_id} нет")
    return user
