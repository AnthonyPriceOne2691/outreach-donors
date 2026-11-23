"""Ключ шифрования секретов кода (`AUTH_TOTP_KEY`): секрет TOTP лежит в базе только зашифрованным.

Секрет кода хранится не хешем, как пароль, а так, чтобы его можно было прочитать: код считается
от самого секрета. Копия базы с открытыми секретами дала бы коды всех учёток, и второй шаг
входа перестал бы быть вторым. Зашифрованные она не даёт, пока ключ лежит не в базе, а в `.env`
сервера, — и в копию базы он не попадает.

Fernet (`cryptography`, уже в зависимостях): AES-128-CBC с HMAC-SHA256. Подделанный или чужим
ключом зашифрованный секрет не расшифровывается вовсе (`InvalidToken`), а не даёт мусор,
который выглядел бы секретом.

**Ключа нет — включить код нельзя** (`TotpKeyMissingError`, 409 словами): секрет нечем
зашифровать, а открытым он в базу не ляжет. Ключа нет, а роли с обязательным кодом заданы, —
сервис не стартует вовсе (`config/startup_checks.check_totp`).

**Ключ сменили — прежние секреты не прочитать** (`TotpKeyMismatchError`): код таких учёток не
проверить, входят они кодом восстановления — он хранится хешем и ключа не требует, — а секрет
сбрасывает админ. Поэтому ключ не меняют без плана: смена ключа — сброс входа с кодом у всех.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from backend.config import access as cfg
from backend.config.startup_checks import TOTP_KEY_HOWTO, ConfigError, totp_key_problem

#: Ключа нет: включить код нечем.
MISSING = (
    "Вход с кодом на сервере не настроен: нет ключа шифрования AUTH_TOTP_KEY — "
    "скажите администратору"
)
#: Ключ не тот, каким секрет шифровали: его сменили после включения кода.
MISMATCH = (
    "Код из приложения сейчас не проверить: ключ шифрования на сервере сменили после включения — "
    "войдите кодом восстановления или попросите администратора сбросить вход с кодом"
)


class TotpKeyMissingError(ConfigError):
    """Ключа шифрования секретов кода нет или он не того вида: код не включить и не проверить."""


class TotpKeyMismatchError(ConfigError):
    """Секрет зашифрован не нынешним ключом: ключ сменили после включения кода."""


def _fernet() -> Fernet:
    """Шифр по ключу из настроек. Вид ключа API сверил на старте (`check_totp`); консоль и
    воркеры его не сверяют — отказ словами и здесь."""
    if not cfg.TOTP_KEY:
        raise TotpKeyMissingError(MISSING)
    problem = totp_key_problem(cfg.TOTP_KEY)
    if problem is not None:
        raise TotpKeyMissingError(f"AUTH_TOTP_KEY {problem} — {TOTP_KEY_HOWTO}")
    return Fernet(cfg.TOTP_KEY)


def ready() -> bool:
    """Задан ли годный ключ — включить код можно."""
    return bool(cfg.TOTP_KEY) and totp_key_problem(cfg.TOTP_KEY) is None


def seal(secret: str) -> str:
    """Секрет кода → строка для базы."""
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def unseal(sealed: str) -> str:
    """Строка из базы → секрет кода. Чужим ключом — отказ словами (`MISMATCH`)."""
    try:
        return _fernet().decrypt(sealed.encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise TotpKeyMismatchError(MISMATCH) from exc
