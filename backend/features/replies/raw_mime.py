"""Сырое письмо (MIME) — в текст, заголовки и вложения.

Платформу приёма можно настроить присылать письмо целиком, одним полем
`email`, вместо разобранных полей. Тогда разбирать его нам — и разбирать
так же полно, как разобрала бы она.

**Разбираются байты, а не строка, и целиком, а не обрезок.** Письмо,
обрезанное до разбора, теряет структуру: граница части, вложение и
окончание текста остаются за обрезом. Обрезается уже прочитанный текст.

**Кодировку каждая часть называет сама,** и способ передачи тоже: текст
в quoted-printable без раскодирования приходил как «Preis f=C3=BCr … 200
=E2=82=AC», и цена в евро не читалась ни моделью, ни человеком.

**Текст без текстовой части берётся из HTML,** как у разобранных писем.

**Вложение — это часть, помеченная вложением, часть с именем файла или
не-текстовая часть** (картинка внутри письма тоже файл). Пересланное
письмо (`message/rfc822`) — одно вложение целиком: его текст не наш ответ,
и разбирать его как ответ значило бы читать чужую переписку за донора.
"""

from __future__ import annotations

import email
import logging
import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message

from backend.features.replies.charsets import decode
from backend.features.replies.inbound import Attachment

logger = logging.getLogger(__name__)

#: Части, которые читаются как текст письма, а не как файл.
_BODY_TYPES = ("text/plain", "text/html")

#: Конец заголовков — первая пустая строка, с любыми переводами строк.
_HEAD_END = re.compile(rb"\r?\n\r?\n")

#: Ошибки, которыми стандартная библиотека отвечает на испорченный заголовок.
#: Письмо от этого не перестаёт быть письмом — теряется один заголовок.
_HEADER_FAILURES = (ValueError, IndexError, TypeError, AttributeError, LookupError)


@dataclass(frozen=True, slots=True)
class RawLetter:
    """Что удалось прочитать из сырого письма."""

    text: str
    html: str
    #: Блок заголовков как есть — в том виде, в каком платформа присылает
    #: его полем `headers`, чтобы разбирать его одним путём.
    headers: str
    to: str
    cc: str
    sender: str
    subject: str
    attachments: tuple[Attachment, ...]


def _header(message: Message, name: str) -> str:
    try:
        value = message.get(name)
    except _HEADER_FAILURES as exc:
        logger.warning("приём: заголовок %s сырого письма не прочитан (%s)", name, exc)
        return ""
    return "" if value is None else str(value)


def _leaves(message: Message) -> list[Message]:
    """Части письма без контейнеров, по порядку. Внутрь пересланного письма
    не заходим: оно вложение, а не часть ответа."""
    found: list[Message] = []
    stack = [message]
    while stack:
        part = stack.pop()
        if part.get_content_maintype() == "multipart" and part.is_multipart():
            payload = part.get_payload()
            if isinstance(payload, list):
                stack.extend(child for child in reversed(payload) if isinstance(child, Message))
            continue
        found.append(part)
    return found


def _filename(part: Message) -> str | None:
    try:
        return part.get_filename()
    except _HEADER_FAILURES as exc:
        logger.warning("приём: имя файла в части сырого письма не прочитано (%s)", exc)
        return None


def _is_body(part: Message) -> bool:
    return (
        part.get_content_type() in _BODY_TYPES
        and part.get_content_disposition() != "attachment"
        and not _filename(part)
    )


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    # Без названной кодировки текст по стандарту латинский; восьмибитный
    # байт в нём читается как UTF-8, а не вышло — как windows-1252.
    text, problem = decode(payload, part.get_content_charset() or "us-ascii")
    if problem is not None:
        logger.warning("приём: часть %s сырого письма — %s", part.get_content_type(), problem)
    return text


def _bytes_of(part: Message) -> bytes | None:
    """Файл части. У пересланного письма файлом становится оно целиком.
    `None` — файла не получить: сведения о нём останутся, а пустой файл
    вместо настоящего выглядел бы принятым."""
    if part.get_content_maintype() != "message" or not part.is_multipart():
        payload = part.get_payload(decode=True)
        return payload if isinstance(payload, bytes) else None
    inner = part.get_payload(0)
    if not isinstance(inner, Message):
        return None
    try:
        return inner.as_bytes()
    except (UnicodeError, ValueError, TypeError, LookupError) as exc:
        # Пересланное письмо с испорченными заголовками обратно не собрать.
        logger.warning("приём: пересланное письмо не собрать в файл (%s)", exc)
        return None


def _attachment(part: Message, number: int) -> Attachment:
    name = _filename(part)
    if not name:
        # Безымянный файл — не повод потерять его: имя по типу и номеру.
        extension = ".eml" if part.get_content_type() == "message/rfc822" else ""
        name = f"вложение-{number}{extension}"
    data = _bytes_of(part)
    return Attachment(
        name=name,
        size=None if data is None else len(data),
        content_type=part.get_content_type(),
        data=data,
    )


def read_raw(data: bytes) -> RawLetter:
    """Прочитать сырое письмо: текст, HTML, заголовки и файлы."""
    message = email.message_from_bytes(data, _class=EmailMessage, policy=policy.default)
    texts: list[str] = []
    htmls: list[str] = []
    files: list[Attachment] = []
    for part in _leaves(message):
        if not _is_body(part):
            files.append(_attachment(part, len(files) + 1))
        elif part.get_content_type() == "text/plain":
            texts.append(_text_of(part))
        else:
            htmls.append(_text_of(part))

    head = _HEAD_END.split(data, maxsplit=1)[0]
    return RawLetter(
        # Текст бывает разрезан на части картинками (так шлёт Apple Mail),
        # и все части — одно письмо.
        text="\n".join(t for t in texts if t.strip()),
        html="\n".join(h for h in htmls if h.strip()),
        headers=head.decode("utf-8", "replace"),
        to=_header(message, "to"),
        cc=_header(message, "cc"),
        sender=_header(message, "from"),
        subject=_header(message, "subject"),
        attachments=tuple(files),
    )
