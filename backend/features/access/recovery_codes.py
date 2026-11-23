"""Коды восстановления: вход без телефона — десять разовых кодов, в базе только хеши.

Телефон теряют, разбивают, меняют. Без запасного пути учётка с кодом входила бы только через
админа, а единственный админ — только через команду на сервере. Коды выдаются при включении
кода, показываются один раз и годятся каждый на один вход — вместо кода из приложения.

**Хеш — SHA-256, а не bcrypt.** Код — 16 случайных знаков из 32 (80 бит): перебрать такой по
хешу из утёкшей базы не выйдет и с быстрым хешем, а bcrypt на каждый из десяти кодов стоил бы
двух с половиной секунд на вход. Пароль человек придумывает сам — ему нужна цена хеша; коду,
который придумал генератор, — нет.

**Алфавит без похожих знаков** (base32 Крокфорда: без «i», «l», «o», «u»): код переписывают с
бумаги. Набранное приводится к виду кода — регистр, пробелы и дефисы не важны, «o» читается как
ноль, «i» и «l» — как единица.

**Код тратится одной командой базы** (`UPDATE … WHERE used_at IS NULL RETURNING`): два входа
одним кодом разом не пройдут оба — второй не найдёт непотраченного.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.access import RecoveryCodeModel

COUNT = 10
LENGTH = 16
GROUP = 4
ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # pragma: allowlist secret — алфавит, не секрет
#: Похожие знаки — к своим: так их читают с бумаги.
_LOOKALIKES = str.maketrans({"o": "0", "i": "1", "l": "1"})


def _fresh() -> str:
    """Один код — группами по четыре через дефис: «k3m9-x2qa-…»."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return "-".join(raw[start : start + GROUP] for start in range(0, LENGTH, GROUP))


def normalize(typed: str) -> str:
    """Набранное → вид кода: строчными, без пробелов и дефисов, похожие знаки — к своим."""
    kept = "".join(char for char in typed.lower() if not char.isspace() and char != "-")
    return kept.translate(_LOOKALIKES)


def digest(typed: str) -> str:
    """Хеш кода для базы — от приведённого вида."""
    return hashlib.sha256(normalize(typed).encode("ascii", errors="replace")).hexdigest()


def looks_like_code(typed: str) -> bool:
    """Похоже ли набранное на код восстановления: 16 знаков алфавита."""
    code = normalize(typed)
    return len(code) == LENGTH and all(char in ALPHABET for char in code)


async def issue(session: AsyncSession, user_id: int) -> list[str]:
    """Выпустить новые десять кодов взамен прежних — и вернуть их открытыми: показать один раз.
    Без коммита — в транзакции включения кода."""
    await drop(session, user_id)
    codes = [_fresh() for _ in range(COUNT)]
    session.add_all(RecoveryCodeModel(user_id=user_id, code_hash=digest(code)) for code in codes)
    await session.flush()
    return codes


async def drop(session: AsyncSession, user_id: int) -> None:
    """Коды учётки — прочь: код выключен или сброшен. Без коммита."""
    await session.execute(delete(RecoveryCodeModel).where(RecoveryCodeModel.user_id == user_id))


async def spend(session: AsyncSession, user_id: int, typed: str, now: datetime) -> int | None:
    """Потратить код: подошёл — сколько непотраченных осталось; не подошёл или уже потрачен —
    `None`. Без коммита."""
    if not looks_like_code(typed):
        return None
    rows = await session.execute(
        update(RecoveryCodeModel)
        .where(
            RecoveryCodeModel.user_id == user_id,
            RecoveryCodeModel.code_hash == digest(typed),
            RecoveryCodeModel.used_at.is_(None),
        )
        .values(used_at=now)
        .returning(RecoveryCodeModel.id)
    )
    if rows.scalar_one_or_none() is None:
        return None
    return await left(session, user_id)


async def left(session: AsyncSession, user_id: int) -> int:
    """Сколько непотраченных кодов у учётки."""
    found = await session.scalar(
        select(func.count())
        .select_from(RecoveryCodeModel)
        .where(RecoveryCodeModel.user_id == user_id, RecoveryCodeModel.used_at.is_(None))
    )
    return int(found or 0)
