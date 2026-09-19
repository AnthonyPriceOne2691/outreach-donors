"""Зависимости маршрутов: сессия базы, кто пришёл и что ему можно.

Правило одно на весь файл: **права берутся из базы на каждом запросе,
а не из пропуска.** В пропуске нет ни роли, ни прав намеренно — иначе
отключённый сотрудник ходит с действующим пропуском до конца суток,
а отобранное право продолжает действовать столько же.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.attempts import LoginAttempts
from backend.features.access.permissions import Actor, require
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.access.tokens import read_token
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.shared.database.base import SessionFactory

#: `auto_error=False` — отказ пишем свой: стандартный отвечает по-английски
#: и не говорит, что делать.
_bearer = HTTPBearer(auto_error=False)

#: Счётчик попыток входа — один на процесс, живёт сколько процесс.
attempts = LoginAttempts()

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


async def db_session() -> AsyncIterator[AsyncSession]:
    """Сессия на запрос. Коммитит обработчик: журнал обязан попасть
    в ту же транзакцию, что и само действие."""
    async with SessionFactory() as session:
        yield session


async def signed_in(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(db_session),
) -> UserModel:
    """Кто пришёл. Отключённая учётка не проходит даже с целым пропуском."""
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Нужен вход: пришлите пропуск заголовком Authorization: Bearer …",
            headers=_UNAUTHORIZED,
        )

    payload = read_token(credentials.credentials)
    user = await AccessRepository(session).by_id(payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Учётка отключена или удалена — войти по этому пропуску нельзя",
            headers=_UNAUTHORIZED,
        )
    return user


async def working_user(user: UserModel = Depends(signed_in)) -> UserModel:
    """Тот же вошедший, но с разовым паролем за спиной.

    Пока разовый пароль не сменён, доступны только «кто я» и смена
    пароля. Иначе требование сменить пароль при первом входе держится
    на вежливости интерфейса: запрос мимо него работает как обычно,
    и пароль, продиктованный голосом, остаётся действующим месяцами.
    """
    if user.must_change_password:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Сначала смените разовый пароль: POST /api/auth/password",
        )
    return user


def needs(permission: Permission) -> Callable[[UserModel], Awaitable[UserModel]]:
    """Зависимость «требуется именованное действие».

    Именно действие, а не роль: по имени роли в обработчике нельзя
    ответить, что может оператор, не прочитав все обработчики подряд.
    """

    async def dependency(user: UserModel = Depends(working_user)) -> UserModel:
        require(actor_of(user), permission)
        return user

    return dependency


def actor(user: UserModel) -> Actor:
    """Модель базы → то, с чем работает ядро."""
    return actor_of(user)
