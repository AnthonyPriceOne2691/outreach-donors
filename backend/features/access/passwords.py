"""Пароли: хеш, проверка и выдача разового.

Пароль хранится только хешем. Это не формальность: база с контактами
и перепиской — то, ради чего сюда полезут, и пароль в открытом виде
превращает одну утечку базы в доступ ко всем остальным сервисам, где
человек взял тот же пароль.

Разовый пароль показывается один раз при заведении учётки и при сбросе.
Восстановления нет: почтовый контур проекта занят рассылкой донорам,
и заводить в нём служебные письма значит смешивать два потока с разной
ценой ошибки.
"""

from __future__ import annotations

import logging
import secrets
import string

import bcrypt

logger = logging.getLogger(__name__)

#: Алфавит разового пароля без похожих друг на друга знаков: пароль
#: диктуют голосом и переписывают руками, а `I`, `l`, `1`, `O` и `0`
#: в этот момент неотличимы.
_ALPHABET = "".join(ch for ch in string.ascii_letters + string.digits if ch not in "Il1O0")
ONE_TIME_LENGTH = 16
MIN_PASSWORD_LENGTH = 10


class WeakPasswordError(ValueError):
    """Пароль короче минимального. Сообщение говорит, что делать."""


def hash_password(plain: str) -> str:
    """Хеш для хранения. Пустой пароль — ошибка, а не пустой хеш."""
    if not plain:
        raise WeakPasswordError("Пароль пуст")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, password_hash: str | None) -> bool:
    """Совпадает ли пароль с хешем. Отсутствие хеша — это «не совпадает»,
    а не исключение: учётка без пароля не должна пускать."""
    if not plain or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError) as exc:
        # Битый хеш в базе — не повод падать на входе, но повод не пустить.
        # И повод сказать: молча такую учётку чинят неделю.
        logger.exception("пароль не проверен — хеш в базе испорчен (%r)", exc)
        return False


def assert_strong_enough(plain: str) -> None:
    """Проверка при смене пароля человеком.

    Требование одно — длина. Правила про заглавные и цифры заставляют
    людей писать `Password1!` и записывать его на бумажке; длина
    работает лучше и не раздражает.
    """
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Пароль короче {MIN_PASSWORD_LENGTH} знаков. "
            "Длина надёжнее сложности: возьмите три-четыре слова подряд"
        )


def generate_one_time() -> str:
    """Разовый пароль. Показывается один раз и хранится только хешем."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(ONE_TIME_LENGTH))
