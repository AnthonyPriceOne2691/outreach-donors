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

**Пропуск, выданный до смены пароля, не действует** (проверка прода
10.10.2026). Пропуск несёт отпечаток пароля учётки на момент выдачи, а «кто я»
на каждом запросе (`api/deps.py`, учётку он и так читает из базы) сверяет его
с нынешним. Без этого сброс пароля админом обещал «старый перестанет
действовать», а пропуск, выданный до сброса, открывал данные до конца суток —
и снова, едва человек сменит разовый пароль. Списка выданных пропусков и
миграции для этого не нужно: хеш пароля меняется при каждой смене — соль
bcrypt новая даже для того же пароля.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from backend.config import access as cfg

ALGORITHM = "HS256"
#: Минимальная длина секрета. Требование стандарта для этой подписи:
#: ключ короче 32 байт ослабляет её до подбора. Проверяем сами — иначе
#: «временный» короткий секрет доживёт до прода.
MIN_SECRET_LENGTH = 32

#: Поле отпечатка пароля в пропуске и его длина: 16 шестнадцатеричных знаков — 64 бита,
#: случайно совпасть после смены пароля нечему.
STAMP_CLAIM = "stamp"
STAMP_LENGTH = 16


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
    #: Отпечаток пароля на момент выдачи (`password_stamp`). `None` — пропуск выдан
    #: до отпечатков: такой доживает свой срок (`password_changed_since`).
    password_stamp: str | None = None


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


def password_stamp(password_hash: str | None) -> str:
    """Отпечаток пароля учётки для пропуска.

    HMAC на секрете подписи, а не сам хеш и не его простой отпечаток: пропуск читает любой,
    у кого он есть (подпись его не прячет), а этот отпечаток без секрета не посчитать, и
    о хеше он ничего не говорит.
    """
    signed = hmac.new(
        _secret().encode("utf-8"), f"password:{password_hash or ''}".encode(), hashlib.sha256
    )
    return signed.hexdigest()[:STAMP_LENGTH]


def create_token(user_id: int, *, password_hash: str | None, now: datetime | None = None) -> str:
    """Выдать пропуск сотруднику. `password_hash` — нынешний хеш пароля учётки: его
    отпечаток едет в пропуске, и смена пароля этот пропуск закрывает."""
    moment = now or datetime.now(UTC)
    expires = moment + timedelta(hours=cfg.TOKEN_TTL_HOURS)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(moment.timestamp()),
            "exp": int(expires.timestamp()),
            STAMP_CLAIM: password_stamp(password_hash),
        },
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

    stamp = claims.get(STAMP_CLAIM)
    return TokenPayload(
        user_id=user_id,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        password_stamp=stamp if isinstance(stamp, str) else None,
    )


def password_changed_since(payload: TokenPayload, password_hash: str | None) -> bool:
    """Сменили ли пароль учётки после выдачи пропуска — тогда пропуск не действует.

    Пропуск без отпечатка выдан до этой правки, когда отпечатков не было: он доживает свой
    срок (сутки), а не обрывается выкаткой — иначе в день выкатки из сервиса выбросило бы
    всех разом. Через сутки после выкатки таких не остаётся. Сброс админом для них и так
    закрывает работу до смены разового пароля (`api/deps.py`, `working_user`).
    """
    if payload.password_stamp is None:
        return False
    return not hmac.compare_digest(payload.password_stamp, password_stamp(password_hash))
