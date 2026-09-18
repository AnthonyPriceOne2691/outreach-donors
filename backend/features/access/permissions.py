"""Кто что может: роль плюс точечные исключения.

Проверка всегда ссылается на именованное действие, а не на роль. Разница
не косметическая: по имени роли в обработчике нельзя ответить на вопрос
«что может оператор», не прочитав все обработчики подряд, — а отвечать
на него придётся каждый раз, когда заводят нового человека.

**Отправка писем не входит в роль оператора.** Её выдают поимённо. Дело
не в недоверии к людям: скомпрометированная учётка обычного сотрудника
не должна превращаться в рассылку с наших доменов. Юниты возвращаются
первого числа, репутация домена — никогда.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.features.core.domain import Permission, UserRole

#: Что даёт роль сама по себе.
ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset(Permission),
    UserRole.OPERATOR: frozenset({Permission.VIEW, Permission.RUN, Permission.SETTINGS}),
}


class AccessDeniedError(PermissionError):
    """Действие запрещено. Сообщение называет действие, а не «нет прав»."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Кто действует. Ровно то, что нужно для проверки прав, — не модель базы.

    Проверка не должна зависеть от сессии базы: так её можно звать
    откуда угодно, включая тесты без базы.
    """

    user_id: int
    role: UserRole
    is_active: bool = True
    #: Точечные исключения поверх роли: `{"send": true}` добавляет
    #: действие, `{"send": false}` отбирает.
    overrides: dict[str, Any] | None = None


def permissions_of(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(actor: Actor, permission: Permission) -> bool:
    """Может ли сотрудник совершить действие.

    Отключённая учётка не может ничего — проверка идёт по базе на каждом
    запросе, а не по пропуску: иначе уволенный сотрудник работает до конца
    суток, пока не истечёт выданный ему пропуск.
    """
    if not actor.is_active:
        return False

    if actor.overrides:
        override = actor.overrides.get(permission.value)
        if override is not None:
            return bool(override)

    return permission in permissions_of(actor.role)


def require(actor: Actor | None, permission: Permission) -> None:
    """Пропустить или отказать. Отказ называет действие."""
    if actor is None:
        raise AccessDeniedError(f"Нужен вход: действие «{permission.value}» требует учётки")
    if not has_permission(actor, permission):
        raise AccessDeniedError(f"Действие «{permission.value}» недоступно этой учётке")
