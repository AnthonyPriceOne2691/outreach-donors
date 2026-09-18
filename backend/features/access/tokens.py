"""Пропуск: выдача и проверка.

Пропуск подписан симметричным ключом (HS256) и живёт сутки. Обновляющего
пропуска нет: при десяти сотрудниках схема с обновлением не окупается,
а долгоживущий украденный пропуск — это доступ к базе контактов
и к отправке.

**Алгоритм подписи указывается явно при проверке.** Без этого библиотека
берёт алгоритм из самого пропуска, и подделыватель просто пишет там
«без подписи». Это классическая дыра, и закрывается она одной строкой.

**Пустой секрет означает отказ, а не проверку без подписи.** Сервис,
которому не хватает настройки, обязан падать сразу и называть, чего
не хватает, — то же правило, что и для ключей провайдеров.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from backend.config import access as cfg

ALGORITHM = "HS256"
#: Минимальная длина секрета. Требование стандарта для этой подписи:
#: ключ короче 32 байт ослабляет её до подбора. Проверяем сами — иначе
#: «временный» короткий секрет доживёт до прода.
MIN_SECRET_LENGTH = 32


class TokenError(RuntimeError):
    """Пропуск не принят: просрочен, подделан или выдан не нами."""


class SecretMissingError(RuntimeError):
    """Секрет подписи не задан. Работать без него нельзя."""


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Что лежит в пропуске. Ролей и прав здесь нет намеренно.

    Права проверяются по базе на каждом запросе: иначе отключённый
    сотрудник ходит с действующим пропуском до конца суток, а отобранное
    право продолжает действовать.
    """

    user_id: int
    issued_at: datetime
    expires_at: datetime


def _secret() -> str:
    secret = cfg.JWT_SECRET
    if not secret:
        raise SecretMissingError(
            "ACCESS_JWT_SECRET не задан — вход работать не может. "
            "Сгенерировать: openssl rand -hex 32"
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_LENGTH:
        raise SecretMissingError(
            f"ACCESS_JWT_SECRET короче {MIN_SECRET_LENGTH} байт — такую подпись "
            "подбирают. Сгенерировать: openssl rand -hex 32"
        )
    return secret


def create_token(user_id: int, *, now: datetime | None = None) -> str:
    """Выдать пропуск сотруднику."""
    moment = now or datetime.now(UTC)
    expires = moment + timedelta(hours=cfg.TOKEN_TTL_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "iat": int(moment.timestamp()), "exp": int(expires.timestamp())},
        _secret(),
        algorithm=ALGORITHM,
    )


def read_token(token: str) -> TokenPayload:
    """Проверить пропуск и достать из него сотрудника."""
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Пропуск просрочен — войдите заново") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Пропуск не принят: {exc}") from exc

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("В пропуске нет сотрудника") from exc

    return TokenPayload(
        user_id=user_id,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )
