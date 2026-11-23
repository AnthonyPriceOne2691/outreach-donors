"""Вход с кодом учётки: кому он обязателен, как сверяется и как включается и снимается.

Пароль крадут — с фишинговой страницы, из чужого менеджера паролей, со снимка экрана. Код из
приложения (`totp.py`) живёт тридцать секунд и только на телефоне хозяина: с одним паролем
вход больше не открывается. Включить себе код может любая учётка; обязательным он становится
для ролей из `AUTH_TOTP_REQUIRED_ROLES` — когда, решает человек при выкатке (по умолчанию — никому).

Вход один для экрана и для API (`POST /api/auth/login`), поэтому и второй шаг один: без него
пропуск не выдаётся никому. Как идут шаги — `login.py` и `code_login.py`; здесь — состояние кода
учётки и сверка.

**Код сверяется так:** шесть цифр — код из приложения по секрету учётки (`totp_key.unseal`), в
пределах шага назад и вперёд, и шаг должен быть позже последнего принятого: тот же код второй раз
не пускает (`users.totp_last_step`, одной командой базы — два запроса разом не пройдут оба). Иначе
— код восстановления (`recovery_codes.py`), разовый. Не подошло — `CodeRefusedError` с причиной
для журнала; считают неудачу вызывающие.

**Включение — в два шага**: `begin` кладёт в учётку новый секрет зашифрованным, ещё не
включённым; `enable` принимает первый код по нему — код включён, поколение пропусков растёт
(выданные пропуска гаснут, как при смене пароля), выпускаются коды восстановления.

**Выключение и сброс** (`switch_off`) снимают секрет, шаг и коды. Сброс (админ, консоль) ещё и
гасит выданные пропуска: телефон потерян — значит, и сеанс на нём не должен жить.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import access as cfg
from backend.config.startup_checks import ConfigError
from backend.features.access import recovery_codes, totp, totp_key
from backend.features.access.challenge import Step
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import UserModel

WRONG = (
    "Код не подошёл — введите новый из приложения; не подходит и он — проверьте время на телефоне"
)
REPEATED = "Этот код уже использован — дождитесь следующего в приложении"
ALREADY_ON = "Вход с кодом уже включён. Сменить телефон — выключите его и включите заново"
NOT_ON = "Вход с кодом у учётки не включён"
NOT_STARTED = "Сначала начните включение — получите ключ для приложения"


class CodeRefusedError(ValueError):
    """Код не подошёл: не тот, уже использован, не код вовсе. `reason` — причина для журнала
    (`LOGIN_FAILED`): человеку — слова отказа, разбирающему — что именно."""

    def __init__(self, said: str, reason: str) -> None:
        super().__init__(said)
        self.reason = reason


class TotpStateError(ValueError):
    """Действие не к нынешнему состоянию кода: включают включённый, выключают невключённый,
    подтверждают неначатое, выключают обязательный. Состояние, а не запрос (409)."""


@dataclass(frozen=True, slots=True)
class Checked:
    """Код подошёл: из приложения или код восстановления — и сколько их осталось."""

    recovery: bool
    codes_left: int | None = None


def required(role: UserRole) -> bool:
    """Обязателен ли вход с кодом для роли (`AUTH_TOTP_REQUIRED_ROLES`)."""
    return role.value in cfg.TOTP_REQUIRED_ROLES


def check_required_roles() -> None:
    """Старт API: в `AUTH_TOTP_REQUIRED_ROLES` — только роли, которые есть. Опечатка («admins»)
    иначе молча не требовала бы кода ни от кого. Конфиг ролей не знает — сверка здесь."""
    known = {role.value for role in UserRole}
    unknown = sorted(cfg.TOTP_REQUIRED_ROLES - known)
    if unknown:
        raise ConfigError(
            f"AUTH_TOTP_REQUIRED_ROLES: ролей {', '.join(unknown)} нет. "
            f"Есть: {', '.join(sorted(known))} — через запятую, например admin"
        )


def enabled(user: UserModel) -> bool:
    """Включён ли у учётки вход с кодом."""
    return user.totp_enabled_at is not None and user.totp_secret is not None


def step_for(user: UserModel) -> Step | None:
    """Какой шаг ждёт вход после верной пары: код включён — код; роль требует, а код не
    включён — включение; иначе — никакого, пропуск сразу."""
    if enabled(user):
        return Step.CODE
    if required(user.role):
        return Step.ENROLL
    return None


async def verify(session: AsyncSession, user: UserModel, typed: str, now: datetime) -> Checked:
    """Сверить второй шаг входа: код из приложения или код восстановления. Не подошло —
    `CodeRefusedError`. Без коммита."""
    if not enabled(user):
        raise TotpStateError(NOT_ON)
    if totp.looks_like_code(typed):
        assert user.totp_secret is not None  # `enabled` выше
        step = totp.matching_step(totp_key.unseal(user.totp_secret), typed, now.timestamp())
        if step is None:
            raise CodeRefusedError(WRONG, "код не подошёл")
        if not await _take_step(session, user.id, step):
            raise CodeRefusedError(REPEATED, "код уже использован")
        return Checked(recovery=False)
    left = await recovery_codes.spend(session, user.id, typed, now)
    if left is None:
        raise CodeRefusedError(WRONG, "код не подошёл")
    return Checked(recovery=True, codes_left=left)


async def _take_step(session: AsyncSession, user_id: int, step: int) -> bool:
    """Принять шаг кода, только если он позже последнего принятого, — одной командой: из двух
    запросов с одним кодом пройдёт один."""
    rows = await session.execute(
        update(UserModel)
        .where(
            UserModel.id == user_id,
            or_(UserModel.totp_last_step.is_(None), UserModel.totp_last_step < step),
        )
        .values(totp_last_step=step)
        .returning(UserModel.id)
    )
    return rows.scalar_one_or_none() is not None


async def begin(session: AsyncSession, user: UserModel) -> str:
    """Начать включение: новый секрет — в учётку зашифрованным, ещё не включённым. Возвращает
    секрет открытым — показать один раз. Ключа нет — `TotpKeyMissingError`. Без коммита."""
    if enabled(user):
        raise TotpStateError(ALREADY_ON)
    secret = totp.new_secret()
    await session.execute(
        update(UserModel)
        .where(UserModel.id == user.id)
        .values(totp_secret=totp_key.seal(secret), totp_enabled_at=None, totp_last_step=None)
    )
    return secret


async def enable(session: AsyncSession, user: UserModel, typed: str, now: datetime) -> list[str]:
    """Принять первый код по начатому секрету: код включён, поколение пропусков растёт, коды
    восстановления выпущены — они и возвращаются, открытыми, показать один раз. Без коммита."""
    if enabled(user):
        raise TotpStateError(ALREADY_ON)
    if user.totp_secret is None:
        raise TotpStateError(NOT_STARTED)
    step = totp.matching_step(totp_key.unseal(user.totp_secret), typed, now.timestamp())
    if step is None:
        raise CodeRefusedError(WRONG, "код включения не подошёл")
    await session.execute(
        update(UserModel)
        .where(UserModel.id == user.id)
        .values(
            totp_enabled_at=now,
            totp_last_step=step,
            token_generation=UserModel.token_generation + 1,
        )
    )
    codes = await recovery_codes.issue(session, user.id)
    await AccessRepository(session).record(
        AuditAction.TOTP_ENABLED,
        author_id=user.id,
        target=f"user:{user.id}",
        details={"email": user.email},
    )
    await session.refresh(user)
    return codes


async def switch_off(
    session: AsyncSession,
    user: UserModel,
    *,
    action: AuditAction,
    author_id: int | None,
    details: dict[str, Any] | None = None,
) -> None:
    """Снять код: секрет, шаг и коды восстановления — прочь, запись журнала `action`. Сброс
    (`TOTP_RESET`) ещё и гасит выданные пропуска учётки. Без коммита."""
    values: dict[str, Any] = {"totp_secret": None, "totp_enabled_at": None, "totp_last_step": None}
    if action is AuditAction.TOTP_RESET:
        values["token_generation"] = UserModel.token_generation + 1
    await session.execute(update(UserModel).where(UserModel.id == user.id).values(**values))
    await recovery_codes.drop(session, user.id)
    await AccessRepository(session).record(
        action,
        author_id=author_id,
        target=f"user:{user.id}",
        details={"email": user.email, **(details or {})},
    )
    await session.refresh(user)
