"""Вход: проверка пары «почта и пароль» и выдача пропуска.

Три правила, каждое из которых стоило кому-то инцидента.

**Отказ не объясняет, что именно не так.** «Нет такой почты» и «неверный
пароль» — разные сообщения для нас и одно для того, кто подбирает:
первое подтверждает, что учётка есть, и превращает подбор в точечный.

**Отключённая учётка не входит**, даже с верным паролем. Иначе уволенный
сотрудник работает до истечения пропуска.

**Неудачная попытка попадает в журнал.** Иначе подбор пароля невидим:
он выглядит как тишина.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.passwords import verify_password
from backend.features.access.repository import AccessRepository, normalize_email
from backend.features.access.tokens import create_token
from backend.features.core.domain import AuditAction, UserRole


class LoginFailedError(RuntimeError):
    """Вход не выполнен. Текст один на все причины — намеренно."""


REFUSAL = "Неверная почта или пароль"


@dataclass(frozen=True, slots=True)
class Session:
    """Итог входа: пропуск и то, что нужно показать сразу."""

    token: str
    user_id: int
    email: str
    role: UserRole
    must_change_password: bool


async def login(session: AsyncSession, email: str, password: str) -> Session:
    """Проверить пару и выдать пропуск. Любая неудача — один и тот же отказ."""
    repository = AccessRepository(session)
    user = await repository.by_email(email)

    if user is None or not verify_password(password, user.password_hash):
        await repository.record(
            AuditAction.LOGIN_FAILED,
            author_id=user.id if user else None,
            details={"email": normalize_email(email), "reason": "пара не подошла"},
        )
        raise LoginFailedError(REFUSAL)

    if not user.is_active:
        await repository.record(
            AuditAction.LOGIN_FAILED,
            author_id=user.id,
            details={"email": user.email, "reason": "учётка отключена"},
        )
        raise LoginFailedError(REFUSAL)

    await repository.note_login(user.id, now=datetime.now(UTC))
    return Session(
        token=create_token(user.id),
        user_id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
    )
