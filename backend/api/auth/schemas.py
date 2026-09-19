"""Что уходит и приходит по маршрутам входа."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.features.access.permissions import Actor, has_permission
from backend.features.core.domain import Permission, UserRole
from backend.features.core.models.access import UserModel


class Credentials(BaseModel):
    """Пара для входа. Проверки формата почты здесь нет намеренно:
    единственный ответ на любой неверный вход — «неверная почта или
    пароль», и подсказка «это не похоже на почту» его бы нарушила."""

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

    @classmethod
    def of(cls, user: UserModel, actor: Actor) -> Me:
        return cls(
            id=user.id,
            email=user.email,
            role=user.role,
            permissions=sorted(p.value for p in Permission if has_permission(actor, p)),
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
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

    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)
