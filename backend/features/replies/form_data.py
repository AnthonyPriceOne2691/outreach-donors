"""Тело вебхука приёма — форма по точным байтам.

**Почему не разбор формы веб-фреймворка.** Starlette читает текстовые поля
как UTF-8, а при ошибке молча как latin-1, и о кодировке письма (поле
`charsets`) не знает — русский в windows-1251 приходил «Çäðàâñòâóéòå»,
евро из windows-1252 терялось. И держит потолок в 1 МБ на поле и 50 файлов
на форму: ответ с картинкой, вставленной в текст, получал 400, платформа
повторяла доставку и бросала письмо — ответ донора пропадал без следа.

Разбирает тот же python-multipart, что и у Starlette, но поля остаются
байтами, пока не станет известна их кодировка (`charsets.py`), а потолок
один — на тело целиком, и проверяет его маршрут до чтения.

**Почему не `email.parser` стандартной библиотеки,** хотя он тоже точен
до байта: он превращает тело в строку и режет её на строки, и на теле
в 29 МБ пик памяти у него 309 МБ против 63 МБ здесь — при потолке тела
в 30 МБ и нескольких повторах платформы подряд это разница между
«принял» и «упал по памяти».

**Недочитанная форма не выбрасывается.** Всё, что успело прийти целиком,
идёт дальше, а обрыв записывается в лог: письмо без последнего поля
лучше, чем письмо, потерянное целиком.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from email.headerregistry import ContentDispositionHeader, HeaderRegistry
from typing import cast
from urllib.parse import parse_qsl

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header

logger = logging.getLogger(__name__)

#: Имена, под которыми платформа кладёт файлы. Файл узнаётся и по имени
#: файла в заголовке части, но безымянное вложение — тоже вложение.
_FILE_FIELD = re.compile(r"attachment\d+", re.IGNORECASE)

_REGISTRY = HeaderRegistry()

#: Чем стандартная библиотека отвечает на заголовок, который не разобрать.
_HEADER_FAILURES = (ValueError, IndexError, TypeError, AttributeError, LookupError)


class FormError(ValueError):
    """Тело не форма: разбирать нечего. Сообщение говорит, что пришло."""


@dataclass(frozen=True, slots=True)
class FormFile:
    """Файл из формы. Имя — как написано в заголовке части, раскодированное."""

    #: Имя поля формы: `attachment1`, `attachment2`…
    field_name: str
    filename: str | None
    content_type: str | None
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class RawForm:
    """Форма как пришла: текстовые поля байтами, файлы отдельно."""

    fields: dict[str, bytes]
    files: tuple[FormFile, ...] = ()
    #: Форма дочитана до закрывающей границы. `False` — обрыв, и последней
    #: части, если она была, здесь нет.
    complete: bool = True


def _disposition(raw: bytes) -> tuple[str | None, str | None]:
    """Имя поля и имя файла из `Content-Disposition` части.

    Имя файла приходит сырым UTF-8, по RFC 2231 (`filename*=UTF-8''…`)
    или закодированным словом (`=?UTF-8?B?…?=`) — стандартная библиотека
    понимает все три, python-multipart — только первое.
    """
    try:
        # Реестр по имени заголовка всегда собирает `ContentDispositionHeader`;
        # в заглушках типов он объявлен общим `BaseHeader`.
        parsed = cast(
            "ContentDispositionHeader",
            _REGISTRY("content-disposition", raw.decode("utf-8", "replace")),
        )
        name, filename = parsed.params.get("name"), parsed.params.get("filename")
    except _HEADER_FAILURES as exc:
        logger.warning("приём: заголовок части формы не разобран (%s) — %r", exc, raw[:200])
        return None, None
    return (
        None if name is None else str(name),
        None if filename is None else str(filename),
    )


@dataclass
class _Collector:
    """Складывает части по мере разбора. Заголовки части — байтами."""

    fields: dict[str, bytes] = field(default_factory=dict)
    files: list[FormFile] = field(default_factory=list)
    finished: bool = False
    _headers: dict[bytes, bytes] = field(default_factory=dict)
    _name: bytearray = field(default_factory=bytearray)
    _value: bytearray = field(default_factory=bytearray)
    _data: bytearray = field(default_factory=bytearray)

    def part_begin(self) -> None:
        self._headers, self._data = {}, bytearray()

    def header_field(self, data: bytes, start: int, end: int) -> None:
        self._name += data[start:end]

    def header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def header_end(self) -> None:
        self._headers[bytes(self._name).strip().lower()] = bytes(self._value).strip()
        self._name, self._value = bytearray(), bytearray()

    def part_data(self, data: bytes, start: int, end: int) -> None:
        self._data += data[start:end]

    def part_end(self) -> None:
        name, filename = _disposition(self._headers.get(b"content-disposition", b""))
        if not name:
            logger.warning("приём: часть формы без имени (%s байт) пропущена", len(self._data))
            return
        if filename is None and not _FILE_FIELD.fullmatch(name):
            self.fields.setdefault(name, bytes(self._data))
            return
        kind = self._headers.get(b"content-type", b"").decode("latin-1").strip()
        self.files.append(FormFile(name, filename, kind or None, bytes(self._data)))

    def end(self) -> None:
        self.finished = True


def _multipart(body: bytes, boundary: bytes | None) -> RawForm:
    if not boundary:
        raise FormError("Тело multipart/form-data без границы частей (boundary)")
    parts = _Collector()
    try:
        parser = MultipartParser(
            boundary,
            {
                "on_part_begin": parts.part_begin,
                "on_header_field": parts.header_field,
                "on_header_value": parts.header_value,
                "on_header_end": parts.header_end,
                "on_part_data": parts.part_data,
                "on_part_end": parts.part_end,
                "on_end": parts.end,
            },
        )
    except FormParserError as exc:
        raise FormError(f"Граница частей формы не годится: {exc}") from exc
    try:
        parser.write(body)
        parser.finalize()
    except FormParserError as exc:
        logger.warning(
            "приём: форма разобрана не до конца (%s) — беру %s полей и %s файлов",
            exc,
            len(parts.fields),
            len(parts.files),
        )
    if not parts.finished:
        logger.warning("приём: форма оборвана до закрывающей границы — последняя часть потеряна")
    return RawForm(fields=parts.fields, files=tuple(parts.files), complete=parts.finished)


def _urlencoded(body: bytes) -> RawForm:
    """Форма без файлов. Платформа так не шлёт, но так шлёт проверка руками
    и тесты — и разбирается она тем же путём, что и настоящая."""
    fields: dict[str, bytes] = {}
    for name, value in parse_qsl(body, keep_blank_values=True):
        fields.setdefault(name.decode("utf-8", "replace"), value)
    return RawForm(fields=fields)


def read_form(body: bytes, content_type: str) -> RawForm:
    """Разобрать тело вебхука. Не форма — `FormError` с тем, что пришло."""
    media, options = parse_options_header(content_type)
    kind = media.decode("latin-1").lower()
    if kind == "multipart/form-data":
        return _multipart(body, options.get(b"boundary"))
    if kind == "application/x-www-form-urlencoded":
        return _urlencoded(body)
    raise FormError(f"Тело вебхука не форма: {kind or 'тип не указан'}")
