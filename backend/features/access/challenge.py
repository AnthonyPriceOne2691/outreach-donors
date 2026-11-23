"""Вызов второго шага входа: короткая подписанная записка «пароль подошёл, ждём код».

Пароль и код приходят двумя запросами, и между ними серверу надо помнить, что пароль уже
подошёл. Хранить это негде и незачем: вызов — подписанная записка на пять минут (`TTL`),
которую экран отдаёт обратно вместе с кодом (`POST /api/auth/login/code`) или с включением
кода (`POST /api/auth/login/enroll…`).

**Как пропуск его не примет ни один маршрут** — три замка, каждого хватает самого по себе:

- подпись своим ключом — производным от секрета пропусков (`HMAC(секрет, «вызов входа»)`), а не
  им самим: под ключом пропуска подпись вызова не сходится («Пропуск не принят»);
- аудитория шага (`aud`): пропуск без аудитории читается, а с ней библиотека отказывает сама;
- нет отпечатка пароля пропуска (`tokens.STAMP_CLAIM`) — у вызова свой (`STAMP`).

И наоборот: пропуск — не вызов, у него нет ни этой подписи, ни аудитории.

**Только для своего шага**: аудитория — `outreach:login:code` или `outreach:login:enroll`, и
читается вызов только с ожидаемой. Вызов на код не начнёт включение кода, и наоборот.

**Годится один раз**: вызов несёт отпечаток учётки (`account_stamp`) — хеш пароля, поколение
пропусков, минуту последнего входа и включения кода. Удачный вход двигает последний вход, и
тот же вызов больше не подходит; смена пароля, включение и сброс кода — тоже. Неверный код
вызов не тратит: ошибки держат счётчик попыток и пауза учётки, как у пароля.

**Отказ один на все причины** — «Вход устарел»: просрочен, подделан, потрачен или учётка с тех
пор изменилась, — сделать в любом случае одно: ввести почту и пароль заново. Причина — в
журнал приложения.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt

from backend.features.access.tokens import ALGORITHM, signing_secret
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)

#: Сколько живёт вызов: хватает найти телефон и набрать код, не хватает, чтобы забытая
#: на чужом экране форма кода ждала до вечера.
TTL = timedelta(minutes=5)
AUDIENCE = "outreach:login:{step}"
#: Отпечаток учётки в вызове — своё имя поля, не `stamp` пропуска.
STAMP = "account"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

STALE = "Вход устарел — введите почту и пароль заново"


class Step(StrEnum):
    """Какой шаг ждёт вход после пароля."""

    #: Код включён — ввести код из приложения или код восстановления.
    CODE = "code"
    #: Роль требует код, а он не включён — включить и ввести первый код.
    ENROLL = "enroll"


class ChallengeError(RuntimeError):
    """Вызов не годится: просрочен, подделан, потрачен, не того шага или учётка с тех пор
    изменилась. Отказ входа (400, `api/errors.py`), как у пароля; войти — заново."""


@dataclass(frozen=True, slots=True)
class Challenge:
    """Что лежит в вызове: чья учётка, какой шаг и её отпечаток на минуту выдачи."""

    user_id: int
    step: Step
    stamp: str


def _key() -> str:
    """Ключ подписи вызовов — производный от секрета пропусков: свой, но без новой переменной."""
    signed = hmac.new(signing_secret().encode("utf-8"), b"outreach:login-challenge", hashlib.sha256)
    return signed.hexdigest()


def _micros(moment: datetime | None) -> str:
    """Минута с точностью до микросекунды целым числом — так её хранит Postgres; пусто — «-»."""
    if moment is None:
        return "-"
    return str((moment - _EPOCH) // timedelta(microseconds=1))


def account_stamp(user: UserModel) -> str:
    """Отпечаток учётки для вызова: меняется со сменой пароля, поколения пропусков, последнего
    входа и включения кода — тогда выданный до этого вызов не подходит."""
    material = "|".join(
        (
            user.password_hash,
            str(user.token_generation),
            _micros(user.last_login_at),
            _micros(user.totp_enabled_at),
        )
    )
    return hmac.new(_key().encode("utf-8"), material.encode(), hashlib.sha256).hexdigest()


def issue(user: UserModel, step: Step, *, now: datetime | None = None) -> str:
    """Выдать вызов шага `step` учётке, чья пара только что подошла."""
    moment = now or datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": int(moment.timestamp()),
            "exp": int((moment + TTL).timestamp()),
            "aud": AUDIENCE.format(step=step.value),
            STAMP: account_stamp(user),
        },
        _key(),
        algorithm=ALGORITHM,
    )


def read(token: str, step: Step) -> Challenge:
    """Проверить вызов шага `step` и достать из него учётку. Отказ — `STALE`, причина — в журнал."""
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE.format(step=step.value),
            options={"require": ["sub", "iat", "exp", "aud", STAMP]},
        )
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        logger.info(
            "вход: вызов второго шага не принят", extra={"step": step.value, "reason": str(exc)}
        )
        raise ChallengeError(STALE) from exc
    return Challenge(user_id=user_id, step=step, stamp=str(claims[STAMP]))


def still_fits(challenge: Challenge, user: UserModel | None) -> UserModel:
    """Учётка вызова — та же, что на минуту выдачи, и может войти; иначе — `STALE`.

    Отключённая учётка — тот же отказ: второй шаг не скажет того, чего не сказал первый."""
    if user is None or not user.is_active:
        raise ChallengeError(STALE)
    if not hmac.compare_digest(challenge.stamp, account_stamp(user)):
        logger.info(
            "вход: вызов второго шага устарел — учётка изменилась после выдачи",
            extra={"user_id": user.id, "step": challenge.step.value},
        )
        raise ChallengeError(STALE)
    return user
