"""Что уходит и приходит по маршрутам учёток."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.features.access.permissions import has_permission
from backend.features.access.repository import actor_of
from backend.features.core.domain import Permission, UserRole
from backend.features.core.models.access import UserModel


def _check_overrides(value: dict[str, Any] | None) -> dict[str, bool] | None:
    """Точечные права — только известные действия и только «да» или «нет».

    Опечатка в имени действия иначе молча ничего не делает: право
    выдано на вид, а запрос отказывает, и разбираться приходится
    в журнале.
    """
    if value is None:
        return None
    known = {p.value for p in Permission}
    unknown = sorted(set(value) - known)
    if unknown:
        raise ValueError(
            f"Неизвестные действия: {', '.join(unknown)}. Известны: {', '.join(sorted(known))}"
        )
    return {name: bool(flag) for name, flag in value.items()}


class UserCard(BaseModel):
    """Учётка в списке и в карточке.

    Показываются обе стороны прав: `permissions` — что человек может
    на самом деле, `overrides` — чем это отличается от его роли.
    Одного `permissions` мало: по нему не видно, выдано право поимённо
    или досталось от роли, а отбирают обратно только первое.
    """

    id: int
    email: str
    role: UserRole
    permissions: list[str]
    overrides: dict[str, bool] = Field(default_factory=dict)
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None

    @classmethod
    def of(cls, user: UserModel) -> UserCard:
        actor = actor_of(user)
        return cls(
            id=user.id,
            email=user.email,
            role=user.role,
            permissions=sorted(p.value for p in Permission if has_permission(actor, p)),
            overrides=_check_overrides(user.permissions) or {},
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
        )


class NewUser(BaseModel):
    """Заведение учётки. Пароля здесь нет: его выдаёт система разовым
    и показывает один раз — придуманный админом пароль он бы диктовал
    голосом, а сотрудник оставлял бы навсегда."""

    email: str = Field(min_length=3, max_length=255)
    role: UserRole


class OneTimePassword(BaseModel):
    """Разовый пароль. Показывается один раз: в базе только хеш,
    и второй раз показать его будет нечем."""

    user: UserCard
    password: str
    note: str = "Пароль показан один раз. При входе система потребует его сменить."


class AccessPatch(BaseModel):
    """Правка доступа: роль, точечные права, включение и выключение.

    Удаления учётки нет нигде намеренно: вместе с ней ушла бы история
    её действий — кто менял пороги, кто отправил письмо.
    """

    role: UserRole | None = None
    is_active: bool | None = None
    permissions: dict[str, Any] | None = None

    @field_validator("permissions")
    @classmethod
    def _known_actions(cls, value: dict[str, Any] | None) -> dict[str, bool] | None:
        return _check_overrides(value)

    @model_validator(mode="after")
    def _not_empty(self) -> AccessPatch:
        if self.role is None and self.is_active is None and self.permissions is None:
            raise ValueError("Нечего менять: укажите роль, права или активность")
        return self
