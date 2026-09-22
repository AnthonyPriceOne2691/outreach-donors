"""Перевод того, что прислала почтовая платформа, в наше входящее письмо.

Платформа приёма — SendGrid Inbound Parse: она отправляет
`multipart/form-data` с полями `from`, `to`, `subject`, `text`, `html`,
`headers`, а в сыром режиме — одно поле `email` с письмом целиком.

**Перевод отделён от конвейера нарочно.** Смена платформы — это правка
одного этого файла, а не всего приёма: поля называются по-разному
у каждого, а «кто ответил и что написал» одинаково у всех.

**Заголовки приходят одной строкой.** Их разбирают здесь, а не выше:
по ним узнаются автоответчик, отказ доставки и цепочка, и оставить их
блоком текста значило бы искать подстроки по всему письму.

**Всё, что пришло, обрезается по длине здесь же.** Граница стоит до
разбора, а не после: письмо на гигабайт не должно занимать память,
даже если мы его потом выбросим.
"""

from __future__ import annotations

import json
import logging
import re
from email import message_from_string
from email.header import decode_header, make_header
from typing import Any

from backend.features.replies.inbound import (
    MAX_ATTACHMENTS,
    MAX_BODY_CHARS,
    Attachment,
    Incoming,
    addresses_in,
)

logger = logging.getLogger(__name__)

#: Заголовки, по которым мы что-то решаем. Остальные не храним: письмо
#: несёт их десятки, и ни один не участвует ни в привязке, ни в разборе.
KEPT_HEADERS = (
    "message-id",
    "in-reply-to",
    "references",
    "return-path",
    "auto-submitted",
    "precedence",
    "x-autoreply",
    "x-autorespond",
    "x-failed-recipients",
    "content-type",
)

_HEADER_LINE = re.compile(r"^([A-Za-z0-9-]+):\s*(.*)$")
_MESSAGE_ID = re.compile(r"<[^>]+>")


def parse_headers(blob: str | None) -> dict[str, str]:
    """Блок заголовков в словарь.

    Продолжения строк (заголовок, перенесённый на следующую строку
    с отступом) приклеиваются к предыдущему: `References` переносится
    почти всегда, и разорванный пополам он не совпадёт ни с чем.
    """
    if not blob:
        return {}

    headers: dict[str, str] = {}
    last: str | None = None
    for line in blob.splitlines():
        if line[:1] in (" ", "\t") and last is not None:
            headers[last] = f"{headers[last]} {line.strip()}"
            continue
        found = _HEADER_LINE.match(line)
        if found is None:
            last = None
            continue
        name = found.group(1).lower()
        last = name if name in KEPT_HEADERS else None
        if last is not None:
            headers[last] = found.group(2).strip()
    return headers


def message_ids_in(value: str) -> tuple[str, ...]:
    """Идентификаторы писем из заголовка: они всегда в угловых скобках."""
    return tuple(_MESSAGE_ID.findall(value or ""))


def decoded(value: str) -> str:
    """Тема письма в читаемый вид: она приходит закодированной, если
    в ней есть что-то, кроме латиницы."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        # Непонятная кодировка — не повод потерять тему целиком, но
        # и не повод молчать: тема в кракозябрах на экране объясняется
        # только этой строкой в логе.
        logger.warning("приём: тему не удалось раскодировать — %r", value[:80])
        return value


def attachments_from(raw: Any) -> tuple[Attachment, ...]:
    """Список вложений из поля `attachment-info`.

    Платформа присылает его словарём JSON: имя, тип и размер каждого.
    Непонятное значение — это ноль вложений, а не отказ: письмо важнее
    списка его файлов.
    """
    if not raw:
        return ()
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        # Письмо важнее списка его файлов, но пропавшие вложения надо
        # считать: «ответ без прайса» и «прайс потерялся» — разное.
        logger.warning("приём: список вложений не разобран — %r", str(raw)[:120])
        return ()
    if not isinstance(info, dict):
        return ()

    found: list[Attachment] = []
    for item in list(info.values())[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        found.append(
            Attachment(
                name=str(item.get("filename") or item.get("name") or "без имени")[:255],
                size=int(item.get("size") or 0),
                content_type=str(item.get("type") or "") or None,
            )
        )
    return tuple(found)


def text_from_raw(raw: str | None) -> str:
    """Текст письма из сырого MIME — на случай, когда платформа прислала
    только его."""
    if not raw:
        return ""
    message = message_from_string(raw[:MAX_BODY_CHARS])
    if not message.is_multipart():
        return str(message.get_payload() or "")

    for part in message.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
            return str(payload or "")
    return ""


def from_form(form: dict[str, Any]) -> Incoming:
    """Собрать входящее письмо из полей платформы."""
    headers = parse_headers(_first(form, "headers"))
    text = _first(form, "text", "plain", "body-plain")
    if not text:
        text = text_from_raw(_first(form, "email"))

    references = message_ids_in(headers.get("references", ""))
    in_reply_to = message_ids_in(headers.get("in-reply-to", ""))
    own_id = message_ids_in(headers.get("message-id", ""))

    return Incoming(
        # Идентификатор письма — то, чем отличается повтор от второго
        # ответа. Нет его — идемпотентности не будет, и это надо видеть.
        message_id=(own_id[0] if own_id else "")[:255],
        to=addresses_in(_first(form, "to")),
        from_email=(addresses_in(_first(form, "from")) or ("",))[0],
        subject=decoded(_first(form, "subject"))[:512],
        text=text[:MAX_BODY_CHARS],
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        references=references,
        headers=headers,
        attachments=attachments_from(form.get("attachment-info")),
    )


def _first(form: dict[str, Any], *names: str) -> str:
    """Первое непустое поле из перечисленных.

    Имён несколько, потому что платформы называют одно и то же
    по-разному, а переписывать конвейер под каждую — не работа.
    """
    for name in names:
        value = form.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return ""
