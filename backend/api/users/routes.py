"""Учётки: список, заведение, правка доступа, сброс пароля.

Весь раздел закрыт одним именованным действием — `users`. Оно есть
только у админа, но проверяется именно действие: появится третья роль,
и матрица прав ответит за неё сама, без правки обработчиков.

Первого админа здесь нет и не будет: он заводится командой при
развёртывании. Открытая регистрация «пока база пуста» — это окно,
в которое успевает зайти чужой.

**Правка доступа и сброс чужого пароля — с паролем админа** (аудит 20.11.2026, №10): пропуск
админа без пароля этих действий не открывает (`administration.confirm_own_password`). Неверный
пароль считает тот же счётчик, что неверный старый при смене своего (`_confirmed`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import deps
from backend.api.deps import db_session, needs
from backend.api.users.schemas import (
    AccessPatch,
    Confirmation,
    NewUser,
    OneTimePassword,
    UserCard,
)
from backend.features.access.administration import (
    NotConfirmedError,
    confirm_own_password,
    create_user,
    reset_password,
    update_access,
)
from backend.features.access.attempts import TooManyAttemptsError
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["учётки"])

#: Один и тот же пропуск на весь раздел.
_admin = Depends(needs(Permission.USERS))


async def _confirmed(author: UserModel, password: str | None) -> None:
    """Подтверждение паролем админа — со счётом неверных, как у смены своего пароля
    (`api/auth/routes.change_password`, аудит 10.10.2026): украденный пропуск иначе подбирал бы
    пароль хозяина без предела, а с паролем — и вход. Ключ тот же: подбор своего пароля — один
    счёт, через какую ручку он ни шёл бы. В журнал действий отказ не пишется, как и неверный
    старый пароль при смене своего, — строка журнала приложения."""
    key = f"password:{author.id}"
    try:
        deps.attempts.check(key)
    except TooManyAttemptsError:
        logger.warning(
            "учётки: попытки подтверждения исчерпаны, пароль даже не проверялся",
            extra={"user_id": author.id},
        )
        raise
    try:
        await confirm_own_password(password, author.password_hash)
    except NotConfirmedError:
        deps.attempts.failed(key)
        logger.warning(
            "учётки: действие не подтверждено — пароль админа не подошёл",
            extra={"user_id": author.id},
        )
        raise
    deps.attempts.succeeded(key)


@router.get("", response_model=list[UserCard], summary="Список учёток")
async def all_users(
    _: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> list[UserCard]:
    users = await AccessRepository(session).all_users()
    return [UserCard.of(user) for user in users]


@router.post(
    "",
    response_model=OneTimePassword,
    status_code=status.HTTP_201_CREATED,
    summary="Завести учётку",
)
async def add_user(
    body: NewUser,
    author: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> OneTimePassword:
    user, password = await create_user(
        session, email=body.email, role=body.role, author=actor_of(author)
    )
    await session.commit()
    return OneTimePassword(user=UserCard.of(user), password=password)


@router.patch("/{user_id}", response_model=UserCard, summary="Роль, права, активность")
async def change_access(
    user_id: int,
    body: AccessPatch,
    author: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> UserCard:
    await _confirmed(author, body.current_password)
    user = await update_access(
        session,
        user_id=user_id,
        author=actor_of(author),
        role=body.role,
        is_active=body.is_active,
        permissions=body.permissions,
    )
    await session.commit()
    return UserCard.of(user)


@router.post(
    "/{user_id}/password",
    response_model=OneTimePassword,
    summary="Сбросить пароль",
)
async def reset(
    user_id: int,
    body: Confirmation | None = None,
    author: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> OneTimePassword:
    await _confirmed(author, body.current_password if body is not None else None)
    user, password = await reset_password(session, user_id=user_id, author=actor_of(author))
    await session.commit()
    return OneTimePassword(user=UserCard.of(user), password=password)
