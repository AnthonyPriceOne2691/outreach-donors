"""Адрес «куда отвечать» с подписанной меткой.

Решение целиком описано в `docs/OUTREACH_THREADS.md`, здесь — исполнение.
Коротко: привязка ответа по адресу отправителя ломается на первом же
пересланном письме, привязка по заголовкам цепочки работает наполовину.
Поэтому поле «куда отвечать» указывает на служебный адрес с меткой:

    anna+m417.7d3a91c2e5@replies.наш-домен

**Метка кодирует номер письма, а не получателя.** Документ говорит
«номер получателя»; письмо строже и даёт то же самое — по нему известны
и диалог, и контакт, и кампания, — а вот по контакту письмо
не восстанавливается, когда их было три.

**Подпись обязательна.** Без неё метку можно угадать перебором и
подсунуть ответ в чужой диалог. Секрет тот же, что у приёма почты
(`OUTREACH_INBOUND_SECRET`), нового хранилища не появляется.

**Локальная часть берётся от отправителя** — чтобы человек, глядя
на «куда отвечать», видел понятное имя, а не служебную строку.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from backend.config import outreach as cfg

#: Длина подписи в шестнадцатеричных знаках. Сорок бит: перебрать нельзя,
#: а адрес остаётся читаемым.
SIGNATURE_LEN = 10

_LABEL_RE = re.compile(rf"^m(\d+)\.([0-9a-f]{{{SIGNATURE_LEN}}})$")


class ReplyAddressError(ValueError):
    """Собрать адрес для ответа нельзя: не настроен поддомен или секрет."""


def _sign(message_id: int, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"m{message_id}".encode(), sha256)
    return digest.hexdigest()[:SIGNATURE_LEN]


def label_for(message_id: int, *, secret: str | None = None) -> str:
    """Подписанная метка письма."""
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not key:
        raise ReplyAddressError(
            "OUTREACH_INBOUND_SECRET не задан — метку в адресе ответа нечем подписать, "
            "а без подписи чужой может подсунуть ответ в чужой диалог"
        )
    return f"m{message_id}.{_sign(message_id, key)}"


def address_for(
    message_id: int,
    *,
    sender_email: str,
    reply_domain: str | None = None,
    secret: str | None = None,
) -> str:
    """Адрес «куда отвечать» для конкретного письма."""
    domain = reply_domain if reply_domain is not None else cfg.REPLY_DOMAIN
    if not domain:
        raise ReplyAddressError(
            "OUTREACH_REPLY_DOMAIN не задан — ответы принимать некуда. "
            "Нужен поддомен, принимающий всю почту без разбора"
        )
    local = sender_email.split("@", 1)[0]
    return f"{local}+{label_for(message_id, secret=secret)}@{domain}"


def message_id_from(address: str, *, secret: str | None = None) -> int | None:
    """Номер письма из адреса, на который пришёл ответ.

    `None` — метки нет или подпись не сходится. Оба случая означают одно:
    привязывать по метке нечего, остаётся запасной путь через заголовки
    цепочки. Разными их делать незачем — действие одинаковое.
    """
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not key:
        return None

    local = address.split("@", 1)[0]
    _, plus, label = local.partition("+")
    if not plus:
        return None

    found = _LABEL_RE.match(label)
    if found is None:
        return None

    message_id = int(found.group(1))
    if not hmac.compare_digest(found.group(2), _sign(message_id, key)):
        return None
    return message_id
