"""Шаги входа с кодом: второй шаг по вызову, включение кода и его выключение владельцем.

Что зовут маршруты (`api/auth/code_routes.py`); правила кода — `second_step.py`, вызов —
`challenge.py`, пропуск и счёт неудач — `login.py`.

**Неверный код — неудача входа, как неверный пароль**: запись `LOGIN_FAILED` с причиной («код
не подошёл»), ещё одна неудача подряд и, с порога, пауза учётки (`login.count_toward_pause`).
Во время паузы код не сверяется — отказ со сроком, как у пароля. `LOGIN` пишется только после
верного кода (`login.open_session`).

**Код восстановления — тем же полем**: шесть цифр — код из приложения, иначе — код
восстановления. Вход им — запись `RECOVERY_CODE_USED` со счётом оставшихся: кончаются коды —
пора выпустить новые (выключить и включить код).

**Включение по своей воле — с паролем** (`api/auth/code_routes.setup`): украденный пропуск иначе
включил бы код на телефон вора и запер бы хозяина его же вторым шагом. Обязательное включение
идёт по вызову — пароль только что подошёл.

**Выключить свой код — паролем и кодом**, и нельзя, если роль его требует: сменить телефон —
попросить админа сбросить, и учётка включит код заново при следующем входе.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import access as cfg
from backend.features.access import second_step, totp
from backend.features.access.challenge import Challenge, still_fits
from backend.features.access.lockout import AccountPausedError, refuse_while_paused
from backend.features.access.login import Session, count_toward_pause, open_session
from backend.features.access.repository import AccessRepository
from backend.features.access.second_step import CodeRefusedError, TotpStateError
from backend.features.access.tokens import create_token
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import UserModel
from backend.features.ops.access_event_words import ROLE_TITLES

logger = logging.getLogger(__name__)


def required_words(role: UserRole) -> str:
    """Отказ выключить обязательный код — с тем, что делать вместо."""
    return (
        f"Для роли «{ROLE_TITLES[role]}» вход с кодом обязателен — выключить его нельзя. "
        "Сменить телефон — попросите админа сбросить вход с кодом"
    )


@dataclass(frozen=True, slots=True)
class Setup:
    """Начатое включение: секрет для приложения — строкой и ссылкой `otpauth://`."""

    secret: str
    uri: str
    issuer: str
    account: str


@dataclass(frozen=True, slots=True)
class Enabled:
    """Код включён: новый пропуск (прежние погасли) и коды восстановления — показать один раз."""

    token: str
    recovery_codes: list[str]


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def _fitting(session: AsyncSession, ticket: Challenge, moment: datetime) -> UserModel:
    """Учётка вызова — та же и не на паузе после неудач подряд. Во время паузы код не
    сверяется, как и пароль (`lockout.py`)."""
    user = still_fits(ticket, await AccessRepository(session).by_id(ticket.user_id))
    try:
        refuse_while_paused(user.locked_until, moment)
    except AccountPausedError:
        logger.warning(
            "вход: учётка на паузе после неудач подряд, код даже не проверялся",
            extra={"user_id": user.id},
        )
        raise
    return user


async def _code_failed(
    repository: AccessRepository, user: UserModel, moment: datetime, reason: str
) -> None:
    """Код не подошёл: запись журнала и ещё одна неудача подряд — тем же счётом, что пароль."""
    await repository.record(
        AuditAction.LOGIN_FAILED,
        author_id=user.id,
        details={"email": user.email, "reason": reason},
    )
    await count_toward_pause(repository, user, moment)


async def finish_with_code(
    session: AsyncSession, ticket: Challenge, typed: str, *, now: datetime | None = None
) -> Session:
    """Второй шаг входа: код из приложения или код восстановления → пропуск. Не подошёл —
    `CodeRefusedError` после записи неудачи (коммит — за маршрутом, отказ его не отменяет)."""
    moment = _now(now)
    repository = AccessRepository(session)
    user = await _fitting(session, ticket, moment)
    try:
        checked = await second_step.verify(session, user, typed, moment)
    except CodeRefusedError as exc:
        await _code_failed(repository, user, moment, exc.reason)
        raise
    if checked.recovery:
        await repository.record(
            AuditAction.RECOVERY_CODE_USED,
            author_id=user.id,
            target=f"user:{user.id}",
            details={"email": user.email, "осталось кодов": checked.codes_left},
        )
    return await open_session(repository, user, moment)


async def start(session: AsyncSession, user: UserModel) -> Setup:
    """Начать включение кода учётке: новый секрет и ссылка для приложения. Без коммита."""
    secret = await second_step.begin(session, user)
    return Setup(
        secret=secret,
        uri=totp.otpauth_uri(secret, account=user.email, issuer=cfg.TOTP_ISSUER),
        issuer=cfg.TOTP_ISSUER,
        account=user.email,
    )


async def start_by_challenge(
    session: AsyncSession, ticket: Challenge, *, now: datetime | None = None
) -> Setup:
    """Обязательное включение: начать по вызову — пара только что подошла."""
    return await start(session, await _fitting(session, ticket, _now(now)))


async def confirm_by_challenge(
    session: AsyncSession, ticket: Challenge, typed: str, *, now: datetime | None = None
) -> tuple[Session, list[str]]:
    """Обязательное включение: первый код по вызову → код включён и вход состоялся. Неверный
    код — неудача входа, как во втором шаге."""
    moment = _now(now)
    repository = AccessRepository(session)
    user = await _fitting(session, ticket, moment)
    try:
        codes = await second_step.enable(session, user, typed, moment)
    except CodeRefusedError as exc:
        await _code_failed(repository, user, moment, exc.reason)
        raise
    return await open_session(repository, user, moment), codes


async def confirm_own(
    session: AsyncSession, user: UserModel, typed: str, *, now: datetime | None = None
) -> Enabled:
    """Включение по своей воле: первый код → код включён. Пропуска учётки, выданные до этого,
    погасли — и этот тоже: в ответе новый. Без коммита."""
    codes = await second_step.enable(session, user, typed, _now(now))
    token = create_token(
        user.id, password_hash=user.password_hash, generation=user.token_generation
    )
    return Enabled(token=token, recovery_codes=codes)


def check_can_disable(user: UserModel) -> None:
    """Выключить свой код можно, только если он включён и роль его не требует — проверка до
    пароля и кода: иначе человек набрал бы оба, чтобы услышать «нельзя»."""
    if second_step.required(user.role):
        raise TotpStateError(required_words(user.role))
    if not second_step.enabled(user):
        raise TotpStateError(second_step.NOT_ON)


async def disable_own(
    session: AsyncSession, user: UserModel, typed: str, *, now: datetime | None = None
) -> None:
    """Выключить свой код — кодом (пароль сверил маршрут). Роль требует код — нельзя. Без
    коммита."""
    check_can_disable(user)
    await second_step.verify(session, user, typed, _now(now))
    await second_step.switch_off(session, user, action=AuditAction.TOTP_DISABLED, author_id=user.id)
