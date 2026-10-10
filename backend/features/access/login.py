"""Вход: проверка пары «почта и пароль» и выдача пропуска.

Три правила, каждое из которых стоило кому-то инцидента.

**Отказ не объясняет, что именно не так.** «Нет такой почты» и «неверный
пароль» — разные сообщения для нас и одно для того, кто подбирает:
первое подтверждает, что учётка есть, и превращает подбор в точечный.

**Отключённая учётка не входит**, даже с верным паролем. Иначе уволенный
сотрудник работает до истечения пропуска.

**Неудачная попытка попадает в журнал.** Иначе подбор пароля невидим:
он выглядит как тишина.

**Время отказа тоже одно** (аудит 10.10.2026). Пароль сверяется и тогда,
когда почты нет, — с подставным хешем той же цены: иначе отказ по
незнакомой почте приходил на проверку bcrypt быстрее, и одинаковый текст
выдавало время ответа.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.passwords import hash_password, verify_password
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


@cache
def _stand_in_hash() -> str:
    """Хеш для почты, которой нет: один на процесс, той же цены, что у настоящих
    (`hash_password`), из случайного пароля — совпасть с ним нечему."""
    return hash_password(secrets.token_urlsafe(32))


def _password_matches(password: str, stored: str | None) -> bool:
    """Сверить пароль с хешем учётки, а нет учётки — с подставным: ответ тот же «нет»,
    но за то же время. Сверка с подставным нужна ради её цены, а не ради ответа."""
    if stored is None:
        verify_password(password, _stand_in_hash())
        return False
    return verify_password(password, stored)


async def login(session: AsyncSession, email: str, password: str) -> Session:
    """Проверить пару и выдать пропуск. Любая неудача — один и тот же отказ."""
    repository = AccessRepository(session)
    user = await repository.by_email(email)

    # bcrypt — в пуле потоков: четверть секунды счёта в цикле событий держала бы
    # остальные запросы, а с подставным хешем считается и каждая незнакомая почта.
    stored = user.password_hash if user is not None else None
    matched = await asyncio.to_thread(_password_matches, password, stored)
    if user is None or not matched:
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
