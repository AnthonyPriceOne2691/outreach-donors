"""Что уходит и приходит по маршрутам входа."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.features.access import second_step
from backend.features.access.challenge import Step
from backend.features.access.permissions import Actor, has_permission
from backend.features.core.domain import Permission, UserRole
from backend.features.core.models.access import UserModel

#: Лишнее поле в запросе входа и смены пароля — отказ «такого поля нет», а не молчание
#: (аудит 19.11.2026): присланное мимо схемы выглядело принятым. Экран шлёт ровно эти поля
#: (`frontend/src/api/auth.ts`).
_ONLY_THESE = ConfigDict(extra="forbid")


class Credentials(BaseModel):
    """Пара для входа. Проверки формата почты здесь нет намеренно:
    единственный ответ на любой неверный вход — «неверная почта или
    пароль», и подсказка «это не похоже на почту» его бы нарушила."""

    model_config = _ONLY_THESE

    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class Me(BaseModel):
    """Кто вошёл и что ему можно.

    Права отдаются готовым списком именованных действий, а не ролью:
    иначе интерфейс повторяет у себя матрицу прав, и однажды она
    разъезжается с настоящей — кнопка есть, запрос отказывает.
    """

    id: int
    email: str
    role: UserRole
    permissions: list[str]
    must_change_password: bool
    last_login_at: datetime | None = None
    #: Вход с кодом из приложения включён (23.11.2026) — и обязателен ли он роли учётки
    #: (`AUTH_TOTP_REQUIRED_ROLES`): обязательный не выключают.
    totp_enabled: bool = False
    totp_required: bool = False

    @classmethod
    def of(cls, user: UserModel, actor: Actor) -> Me:
        return cls(
            id=user.id,
            email=user.email,
            role=user.role,
            permissions=sorted(p.value for p in Permission if has_permission(actor, p)),
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
            totp_enabled=second_step.enabled(user),
            totp_required=second_step.required(user.role),
        )


class SignedIn(BaseModel):
    """Итог входа: пропуск и то, что показать сразу.

    Пропуск и карточка приходят вместе, одним ответом: интерфейсу они
    нужны оба в первую же секунду, а два запроса подряд дают промежуток,
    в котором непонятно, что рисовать.
    """

    token: str
    token_type: str = "bearer"  # noqa: S105 — вид пропуска, не секрет
    expires_in_hours: int
    user: Me


class PasswordChange(BaseModel):
    """Смена своего пароля. Старый обязателен: без него украденный
    пропуск превращается в постоянный доступ."""

    model_config = _ONLY_THESE

    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


#: Поля вызова и кода в запросах второго шага: вызов — подписанная строка (`challenge.py`), код —
#: шесть цифр или код восстановления, с пробелами и дефисами как набран.
_CHALLENGE = Field(min_length=1, max_length=2048)
_CODE = Field(min_length=1, max_length=64)
_PASSWORD = Field(min_length=1, max_length=1024)


class SecondStep(BaseModel):
    """Ответ входа, когда пара подошла, а пропуска ещё нет (403 с `X-Access-Refusal:
    second-step`): какой шаг дальше и вызов к нему. `detail` — для экрана, который шагов не знает:
    он покажет его как отказ входа, словами, что делать."""

    detail: str
    step: Step
    challenge: str
    expires_in_minutes: int


class CodeEntry(BaseModel):
    """Второй шаг по вызову: код из приложения или код восстановления."""

    model_config = _ONLY_THESE

    challenge: str = _CHALLENGE
    code: str = _CODE


class ChallengeEntry(BaseModel):
    """Начать обязательное включение кода по вызову."""

    model_config = _ONLY_THESE

    challenge: str = _CHALLENGE


class PasswordEntry(BaseModel):
    """Начать включение кода по своей воле — с паролем: украденный пропуск иначе включил бы код
    на телефон вора."""

    model_config = _ONLY_THESE

    password: str = _PASSWORD


class CodeOnly(BaseModel):
    """Первый код по начатому секрету — включение по своей воле."""

    model_config = _ONLY_THESE

    code: str = _CODE


class CodeOff(BaseModel):
    """Выключить свой код: пароль и код (из приложения или восстановления)."""

    model_config = _ONLY_THESE

    password: str = _PASSWORD
    code: str = _CODE


class TotpSetup(BaseModel):
    """Секрет для приложения: строкой — переписать руками, ссылкой `otpauth://` — открыть на
    телефоне (QR-код — та же ссылка картинкой). Показывается один раз: в базе он зашифрован."""

    secret: str
    uri: str
    issuer: str
    account: str


def _codes_note() -> str:
    return (
        "Коды восстановления показаны один раз: каждый пускает один раз вместо кода из "
        "приложения. Сохраните их там, где телефона нет, — в менеджере паролей или на бумаге."
    )


class TotpEnabled(SignedIn):
    """Код включён: новый пропуск с карточкой, как у входа (прежние пропуска учётки погасли), и
    коды восстановления — один раз."""

    recovery_codes: list[str]
    note: str = Field(default_factory=_codes_note)
