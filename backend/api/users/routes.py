"""Учётки: список, заведение, правка доступа, сброс пароля.

Весь раздел закрыт одним именованным действием — `users`. Оно есть
только у админа, но проверяется именно действие: появится третья роль,
и матрица прав ответит за неё сама, без правки обработчиков.

Первого админа здесь нет и не будет: он заводится командой при
развёртывании. Открытая регистрация «пока база пуста» — это окно,
в которое успевает зайти чужой.

**Правка доступа и сброс чужого пароля — с паролем админа** (аудит 20.11.2026, №10): пропуск
админа без пароля этих действий не открывает (`administration.confirm_own_password`). Неверный
пароль считает тот же счётчик, что неверный старый при смене своего (`_confirmed`). Сброс чужого
входа с кодом (23.11.2026) — так же.
"""

from __future__ import annotations

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
    create_user,
    reset_password,
    reset_totp,
    update_access,
)
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel

router = APIRouter(prefix="/users", tags=["учётки"])

#: Один и тот же пропуск на весь раздел.
_admin = Depends(needs(Permission.USERS))


async def _confirmed(author: UserModel, password: str | None) -> None:
    """Подтверждение паролем админа — со счётом неверных, как у смены своего пароля
    (`deps.confirm_password`, аудит 10.10.2026)."""
    await deps.confirm_password(author, password, place="учётки")


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


@router.post(
    "/{user_id}/totp/reset",
    response_model=UserCard,
    summary="Сбросить вход с кодом",
)
async def reset_code(
    user_id: int,
    body: Confirmation | None = None,
    author: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> UserCard:
    """Телефон потерян или сменился: секрет и коды восстановления учётки удалены, её пропуска
    погасли. Роль требует код — учётка включит его заново при следующем входе."""
    await _confirmed(author, body.current_password if body is not None else None)
    user = await reset_totp(session, user_id=user_id, author=actor_of(author))
    await session.commit()
    return UserCard.of(user)
