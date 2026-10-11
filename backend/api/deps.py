"""Зависимости маршрутов: сессия базы, кто пришёл и что ему можно.

Правило одно на весь файл: **права берутся из базы на каждом запросе,
а не из пропуска.** В пропуске нет ни роли, ни прав намеренно — иначе
отключённый сотрудник ходит с действующим пропуском до конца суток,
а отобранное право продолжает действовать столько же. Так же и пароль:
пропуск несёт только его отпечаток, и смена пароля закрывает выданные до неё
пропуска (`access/tokens.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.attempts import LoginAttempts
from backend.features.access.permissions import Actor, OneTimePasswordError, require
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.access.tokens import (
    TOKEN_HEADER,
    TokenPayload,
    password_changed_since,
    read_token,
)
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.shared.database.base import SessionFactory

#: Пропуск — своим заголовком (`TOKEN_HEADER`): `Authorization` на /api/ несёт пароль прокси.
#: `auto_error=False` — отказ пишем свой: стандартный отвечает по-английски и не говорит,
#: что делать.
_pass = APIKeyHeader(name=TOKEN_HEADER, auto_error=False)
#: Прежний путь — `Authorization: Bearer`: им ходят консоль /api/docs и тесты. Снаружи прокси
#: его не пропустит — там `Authorization` занят паролем прокси и до сервера не доходит.
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
    token: str | None = Depends(_pass),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(db_session),
) -> UserModel:
    """Кто пришёл. Отключённая учётка не проходит даже с целым пропуском."""
    given = token or (credentials.credentials if credentials else None)
    if not given:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"Нужен вход: пришлите пропуск заголовком {TOKEN_HEADER}",
            headers=_UNAUTHORIZED,
        )

    payload = read_token(given)
    return _admitted(await AccessRepository(session).by_id(payload.user_id), payload)


def _admitted(user: UserModel | None, payload: TokenPayload) -> UserModel:
    """Учётка пропуска — или отказ 401 словами: учётки нет или она отключена; пароль
    сменили после выдачи пропуска.

    Второе — с проверки прода 10.10.2026: сброс админом и смена своего пароля закрывают
    выданные пропуска, а окно сброса до того обещало «старый перестанет действовать»,
    пока пропуск, выданный до сброса, открывал данные до конца суток.
    """
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Учётка отключена или удалена — войти по этому пропуску нельзя",
            headers=_UNAUTHORIZED,
        )
    if password_changed_since(payload, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Пароль учётки сменили — этот пропуск больше не действует, войдите заново",
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
        # Словами и с признаком отказа (`OneTimePasswordError.refusal`): экран по нему уводит
        # на смену пароля, а не показывает метод и путь (проверка прода 10.10.2026).
        raise OneTimePasswordError(
            "Сначала смените разовый пароль: пока он не сменён, остальное закрыто"
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
