"""Вход, «кто я» и смена своего пароля.

Три маршрута, и все три доступны без прав: правами меряется работа,
а это — сам доступ к ней.

**«Кто я» и смена пароля работают с разовым паролем за спиной.**
Остальные маршруты — нет: иначе требование сменить выданный пароль
держится на вежливости интерфейса.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import deps
from backend.api.auth.schemas import Credentials, Me, PasswordChange, SignedIn
from backend.api.deps import db_session, signed_in
from backend.config import access as cfg
from backend.features.access.administration import change_own_password
from backend.features.access.attempts import TooManyAttemptsError
from backend.features.access.login import LoginFailedError, login
from backend.features.access.repository import actor_of, normalize_email
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["вход"])


def _keys(request: Request, email: str) -> tuple[str, str]:
    """Ключи счётчика попыток: почта и адрес обращения.

    Адрес берётся из соединения. За обратным прокси это его адрес,
    поэтому на сервере uvicorn запускается с `--proxy-headers`, а nginx
    передаёт настоящий — иначе весь интернет для счётчика выглядит одним
    клиентом, и первый же перебор закрывает вход всем сразу.
    """
    address = request.client.host if request.client else "адрес неизвестен"
    return f"email:{normalize_email(email)}", f"addr:{address}"


@router.post("/login", response_model=SignedIn, summary="Вход")
async def sign_in(
    body: Credentials,
    request: Request,
    session: AsyncSession = Depends(db_session),
) -> SignedIn:
    """Проверить пару и выдать пропуск."""
    keys = _keys(request, body.email)
    # Счётчик берётся через модуль, а не по имени: так его можно
    # подменить целиком — в тестах и при переезде в общее хранилище.
    try:
        deps.attempts.check(*keys)
    except TooManyAttemptsError:
        # Остановленная попытка до базы не доходит и в журнал не пишется —
        # иначе перебор писал бы в базу столько строк, сколько запросов.
        # Но и молчать нельзя: в журнале подбор выглядел бы как десять
        # попыток и тишина, то есть как прекратившийся. Живой прогон
        # 19.09.2026 это и показал — десять записей, а попыток было
        # двенадцать.
        logger.warning(
            "вход: попытки исчерпаны, пароль даже не проверялся",
            extra={"email": normalize_email(body.email), "attempt_key": keys[1]},
        )
        raise

    try:
        opened = await login(session, body.email, body.password)
    except LoginFailedError:
        deps.attempts.failed(*keys)
        # Запись о неудачной попытке обязана пережить отказ: без коммита
        # она откатывается вместе с ним, и подбор пароля выглядит как
        # тишина — ровно то, ради чего эта запись и делается.
        await session.commit()
        raise

    deps.attempts.succeeded(keys[0])
    await session.commit()

    user = await session.get(UserModel, opened.user_id)
    assert user is not None  # только что вошли этой учёткой
    return SignedIn(
        token=opened.token,
        expires_in_hours=cfg.TOKEN_TTL_HOURS,
        user=Me.of(user, actor_of(user)),
    )


@router.get("/me", response_model=Me, summary="Кто я и что мне можно")
async def me(user: UserModel = Depends(signed_in)) -> Me:
    return Me.of(user, actor_of(user))


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сменить свой пароль",
)
async def change_password(
    body: PasswordChange,
    user: UserModel = Depends(signed_in),
    session: AsyncSession = Depends(db_session),
) -> None:
    await change_own_password(session, user_id=user.id, current=body.current, new=body.new)
    await session.commit()
