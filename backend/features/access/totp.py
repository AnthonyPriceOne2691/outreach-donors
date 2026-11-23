"""Код из приложения-аутентификатора: TOTP по RFC 6238 на стандартной библиотеке.

Код — шесть цифр, которые телефон считает сам из общего с сервером секрета и нынешнего
времени: HMAC-SHA1 номера тридцатисекундного шага, усечённый до цифр (RFC 4226, разд. 5.3).
Своей библиотеки нет намеренно: счёт — двадцать строк `hmac`, `hashlib`, `struct` и
`base64`, а чужой пакет тянул бы обновления и проверку цепочки поставки ради них. Верность
счёта держат векторы RFC 6238 (приложение B) в тестах.

**Параметры — те, что понимает любое приложение**: SHA1, шесть цифр, шаг 30 секунд. Другие
приложения частью молча не поддерживают — человек видел бы код, который не подходит.

**Допуск — шаг назад и шаг вперёд** (`WINDOW`): часы телефона уходят, а код, набранный на
исходе окна, приходит в следующем. Подбирающему это даёт три подходящих кода из миллиона
вместо одного; остальное держат счётчик попыток и пауза учётки (`attempts.py`, `lockout.py`).

**Повтор кода решает не этот модуль**: `matching_step` называет шаг, которому код подошёл, а
второй раз тот же шаг не пускает учётка (`users.totp_last_step`, `second_step.py`).
"""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
from urllib.parse import quote, urlencode

#: Шаг времени в секундах и длина кода — умолчания всех приложений.
STEP_SECONDS = 30
DIGITS = 6
#: Допуск часов телефона в шагах — в каждую сторону.
WINDOW = 1
#: Длина секрета: 20 байт — 160 бит, длина выхода SHA1 (RFC 4226, разд. 4, R6).
SECRET_BYTES = 20


def new_secret() -> str:
    """Новый секрет — base32 заглавными без «=» в конце: так его ждут приложения и ссылка
    `otpauth://` (32 знака на 20 байт — без остатка, «=» и не нужен)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    """Секрет base32 → байты ключа HMAC. Пробелы и регистр — как набрал человек."""
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    """Код HOTP (RFC 4226, разд. 5.3): HMAC-SHA1 счётчика восемью байтами, динамическое
    усечение до 31 бита и остаток от деления на 10^digits — с нулями впереди."""
    mac = hmac.digest(key, struct.pack(">Q", counter), "sha1")
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def step_at(moment: float) -> int:
    """Номер шага времени для секунд эпохи (RFC 6238, разд. 4: T0 = 0)."""
    return int(moment // STEP_SECONDS)


def code_at(secret: str, moment: float, digits: int = DIGITS) -> str:
    """Код, который телефон покажет в эту секунду."""
    return hotp(_key(secret), step_at(moment), digits)


def cleaned(typed: str) -> str:
    """Набранный код без пробелов и дефисов: «123 456» приложения так и показывают."""
    return typed.replace(" ", "").replace("-", "").strip()


def looks_like_code(typed: str) -> bool:
    """Шесть цифр ASCII — код из приложения; иначе это код восстановления или опечатка.
    `isdigit` без `isascii` принял бы и арабские цифры."""
    code = cleaned(typed)
    return len(code) == DIGITS and code.isascii() and code.isdigit()


def matching_step(secret: str, typed: str, moment: float) -> int | None:
    """Шаг, которому код подходит в пределах допуска, — или `None`.

    Каждый шаг окна сверяется `compare_digest` и до конца, без раннего выхода: время ответа не
    говорит, на каком шаге код сошёлся. Совпадут два шага (раз на миллион) — берётся поздний:
    он закрывает повтор шире."""
    if not looks_like_code(typed):
        return None
    code = cleaned(typed)
    key = _key(secret)
    now = step_at(moment)
    found: int | None = None
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(hotp(key, step), code):
            found = step
    return found


def otpauth_uri(secret: str, *, account: str, issuer: str) -> str:
    """Ссылка `otpauth://totp/…` (формат ключа Google Authenticator): на телефоне открывает
    приложение и заводит в нём учётку; QR-код — та же строка картинкой.

    Подпись — «издатель:почта», издатель ещё и полем `issuer`: приложения читают кто что.
    Пробелы — `%20`, а не «+»: «+» часть приложений показывает как есть."""
    label = f"{quote(issuer, safe='')}:{quote(account, safe='@')}"
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"
