"""Перевод того, что прислала почтовая платформа, в наше входящее письмо.

Платформа приёма — SendGrid Inbound Parse: она отправляет
`multipart/form-data` с полями `from`, `to`, `cc`, `subject`, `text`, `html`,
`headers`, `envelope`, `charsets`, списком вложений `attachment-info` и самими
файлами `attachment1…N`, а в сыром режиме — поле `email` с письмом целиком.

**Перевод отделён от конвейера нарочно.** Смена платформы — это правка
одного этого файла, а не всего приёма: поля называются по-разному
у каждого, а «кто ответил и что написал» одинаково у всех.

**Заголовки приходят одной строкой.** Их разбирают здесь, а не выше:
по ним узнаются автоответчик, отказ доставки и цепочка, и оставить их
блоком текста значило бы искать подстроки по всему письму.

**Длина режется после чтения, а не до.** Тело целиком ограничено ещё
в маршруте, до чтения; здесь режется уже прочитанный текст — обрезок байтов
до разбора терял бы структуру письма и рвал многобайтные буквы.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from email.header import decode_header, make_header
from typing import Any

from backend.features.replies.charsets import decode_fields
from backend.features.replies.form_data import FormFile, RawForm
from backend.features.replies.html_text import text_from_html
from backend.features.replies.inbound import (
    MAX_BODY_CHARS,
    Attachment,
    Incoming,
    addresses_in,
    storable,
)
from backend.features.replies.raw_mime import RawLetter, read_raw

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
    # Служебные признаки рассылок и автоответов: их читают правила вида
    # ответа (`classify`) — автоответчик Exchange, список рассылки, адрес
    # для ответа, отличный от отправителя.
    "x-auto-response-suppress",
    "list-id",
    "list-unsubscribe",
    "reply-to",
)

_HEADER_LINE = re.compile(r"^([A-Za-z0-9-]+):\s*(.*)$")
_MESSAGE_ID = re.compile(r"<[^>]+>")
#: Разделитель пути в имени файла: старые почтовые программы присылают
#: имя вместе с папкой отправителя.
_PATH = re.compile(r"[\\/]")


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


def file_name(raw: str | None) -> str:
    """Имя файла, которое можно показать, сохранить и отдать.

    Закодированное слово (`=?UTF-8?B?…?=`) раскодируется, путь отбрасывается,
    невидимые символы убираются: с символом смены направления (U+202E)
    внутри имя «прайс…fdp.exe» читается глазом как «прайсexe.pdf», а проверка
    опасного расширения должна видеть то же, что система, которая файл откроет.
    """
    name = _PATH.split(storable(decoded(raw or "")))[-1]
    name = "".join(char for char in name if char.isprintable()).strip()
    return (name or "без имени")[:255]


def _attachment_info(raw: str | None) -> dict[str, dict[str, Any]]:
    """Поле `attachment-info`: «attachment1 → имя, тип». Размера в нём нет."""
    if not raw:
        return {}
    try:
        info = json.loads(raw)
    except ValueError:
        # Письмо важнее списка его файлов, но пропавшие имена надо
        # считать: «ответ без прайса» и «прайс потерялся» — разное.
        logger.warning("приём: список вложений не разобран — %r", raw[:120])
        return {}
    if not isinstance(info, dict):
        logger.warning("приём: список вложений не словарь — %r", raw[:120])
        return {}
    return {str(key): value for key, value in info.items() if isinstance(value, dict)}


def _name_said(meta: Mapping[str, Any], fallback: str | None = None) -> str:
    return str(meta.get("filename") or meta.get("name") or fallback or "")


def _type_said(meta: Mapping[str, Any], fallback: str | None = None) -> str | None:
    return str(meta.get("type") or fallback or "") or None


#: Больше размер в колонку не ляжет (целое в базе), а названный больше
#: тела запроса — заведомо не размер пришедшего файла.
_SIZE_CEILING = 2**31 - 1


def _size_said(meta: Mapping[str, Any]) -> int | None:
    """Размер, если его назвали. Платформа приёма его не называет, и тогда
    размер неизвестен, а не ноль. Несуразное число — тоже «неизвестен»:
    упавшая запись ответа стоила бы письма целиком."""
    said = str(meta.get("size", ""))
    return int(said) if said.isdigit() and int(said) <= _SIZE_CEILING else None


def _count_checked(declared: str | None, found: int) -> None:
    """Сколько файлов платформа назвала полем `attachments` и сколько пришло.

    Названный, но не пришедший файл обычно виден по списку вложений, но
    списка может не быть вовсе, — и тогда о потере говорит только этот лог.
    """
    said = (declared or "").strip()
    if said.isdigit() and int(said) > found:
        logger.warning("приём: платформа назвала вложений %s, дошло сведений о %s", said, found)


def attachments_from(
    raw_info: str | None, files: Sequence[FormFile] = ()
) -> tuple[Attachment, ...]:
    """Вложения: файлы формы с именами из `attachment-info`.

    Имя берётся из списка вложений (там оно в UTF-8 целиком), а нет его —
    из заголовка части. Размер — настоящий, по байтам. Названный в списке
    файл, которого в форме нет, остаётся сведениями без файла: так видно,
    что прайс был и потерялся, а не что его не присылали.
    """
    info = _attachment_info(raw_info)
    found: list[Attachment] = []
    for file in files:
        meta = info.pop(file.field_name, {})
        found.append(
            Attachment(
                name=_name_said(meta, file.filename),
                size=len(file.data),
                content_type=_type_said(meta, file.content_type),
                data=file.data,
            )
        )
    found.extend(
        Attachment(name=_name_said(meta), size=_size_said(meta), content_type=_type_said(meta))
        for meta in info.values()
    )
    return tuple(found)


def _cleaned(attachment: Attachment) -> Attachment:
    kind = storable(attachment.content_type or "").strip()[:255]
    return replace(attachment, name=file_name(attachment.name), content_type=kind or None)


def _envelope(raw: str | None) -> tuple[tuple[str, ...], str]:
    """Конверт письма: на какие адреса его доставили и обратный адрес.

    Адрес доставки есть только здесь, когда нас поставили в скрытую копию
    или письмо переслали: в заголовках «кому» и «копия» его тогда нет.
    """
    if not raw:
        return (), ""
    try:
        found = json.loads(raw)
    except ValueError:
        logger.warning("приём: конверт письма не JSON — %r", raw[:120])
        return (), ""
    if not isinstance(found, dict):
        return (), ""
    to = found.get("to")
    listed = to if isinstance(to, list) else [to]
    return (
        tuple(address for item in listed for address in addresses_in(str(item or ""))),
        str(found.get("from") or ""),
    )


def _body_text(fields: Mapping[str, str]) -> str:
    """Текст письма: текстовая часть, а нет её — из HTML.

    Переводы строк — к одному виду: почта шлёт CRLF, и «\r» в конце строки
    мешал бы правилам, которые узнают цитату и подпись по концу строки.
    """
    text = _first(fields, "text", "plain", "body-plain")
    if not text:
        html = _first(fields, "html", "body-html")
        text = text_from_html(html, limit=MAX_BODY_CHARS) if html else ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _merged(fields: Mapping[str, str], letter: RawLetter | None) -> dict[str, str]:
    """Поля платформы поверх прочитанного из сырого письма.

    Разобранные поля сильнее: их платформа уже прочитала. Сырое письмо
    дополняет то, чего в полях нет, — в сыром режиме это почти всё.
    """
    if letter is None:
        return dict(fields)
    merged = {
        "headers": letter.headers,
        "to": letter.to,
        "cc": letter.cc,
        "from": letter.sender,
        "subject": letter.subject,
        "text": letter.text,
        "html": letter.html,
    }
    merged.update({name: value for name, value in fields.items() if value.strip()})
    return merged


def from_form(
    fields: Mapping[str, str],
    *,
    files: Sequence[FormFile] = (),
    letter: RawLetter | None = None,
) -> Incoming:
    """Собрать входящее письмо из полей платформы и, если есть, сырого письма."""
    if letter is None and _first(fields, "email"):
        # Сырое письмо, пришедшее строкой (проверка руками, старый путь).
        letter = read_raw(fields["email"].encode("utf-8", "surrogateescape"))
    form = _merged(fields, letter)
    headers = parse_headers(_first(form, "headers"))
    envelope_to, envelope_from = _envelope(_first(form, "envelope"))
    senders = addresses_in(_first(form, "from")) or addresses_in(envelope_from)

    references = message_ids_in(headers.get("references", ""))
    in_reply_to = message_ids_in(headers.get("in-reply-to", ""))
    own_id = message_ids_in(headers.get("message-id", ""))
    attachments = (
        *attachments_from(form.get("attachment-info"), files),
        *(letter.attachments if letter else ()),
    )
    _count_checked(form.get("attachments"), len(attachments))

    return Incoming(
        # Идентификатор письма — то, чем отличается повтор от второго
        # ответа. Нет его — идемпотентности не будет, и это надо видеть.
        message_id=storable(own_id[0] if own_id else "")[:255],
        to=tuple(
            dict.fromkeys(
                (*envelope_to, *addresses_in(_first(form, "to")), *addresses_in(_first(form, "cc")))
            )
        ),
        # Без «от кого» в заголовке остаётся обратный адрес конверта:
        # письмо без отправителя не принимается вовсе, и терять его из-за
        # испорченного заголовка — хуже, чем взять адрес из конверта.
        from_email=storable(senders[0] if senders else ""),
        subject=storable(decoded(_first(form, "subject")))[:512],
        text=storable(_body_text(form))[:MAX_BODY_CHARS],
        in_reply_to=storable(in_reply_to[0]) if in_reply_to else None,
        references=tuple(storable(ref) for ref in references),
        headers={name: storable(value) for name, value in headers.items()},
        attachments=tuple(_cleaned(attachment) for attachment in attachments),
    )


def incoming_from(form: RawForm) -> Incoming:
    """Входящее письмо из формы вебхука: поля — в своей кодировке, сырое
    письмо — байтами, файлы — как есть."""
    raw = form.fields.get("email")
    fields = decode_fields({name: value for name, value in form.fields.items() if name != "email"})
    letter = read_raw(raw) if raw else None
    return from_form(fields, files=form.files, letter=letter)


def _first(form: Mapping[str, Any], *names: str) -> str:
    """Первое непустое поле из перечисленных.

    Имён несколько, потому что платформы называют одно и то же
    по-разному, а переписывать конвейер под каждую — не работа.
    """
    for name in names:
        value = form.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return ""
