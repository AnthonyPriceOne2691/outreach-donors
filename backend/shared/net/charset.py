"""Кодировка страницы, которую сайт не назвал в заголовке ответа.

httpx 0.28 без charset в `Content-Type` читает тело как utf-8, и страница в cp1251, где
кодировка названа только в `<meta>`, превращается в «��������» — это и видела модель.
Браузер в таком случае смотрит `<meta charset>` и `<meta http-equiv="Content-Type">`
в начале документа; так же делает `page_encoding` — `default_encoding` клиента
`url_guard.guarded_client`. httpx зовёт его, только когда charset в заголовке нет или он
незнакомый, так что charset из заголовка по-прежнему главнее.
"""

from __future__ import annotations

import codecs
import logging
import re

logger = logging.getLogger(__name__)

#: Сколько байт начала страницы смотрим. HTML требует `<meta>` с кодировкой в первых
#: 1024 байтах; берём с запасом на длинный `<head>`.
SNIFF_BYTES = 4096

#: `charset=` внутри тега `<meta>`: и `<meta charset=…>`, и `content="…; charset=…"`
#: у `http-equiv`, в любом порядке атрибутов.
_META_CHARSET = re.compile(rb"""<meta\b[^>]*?\bcharset\s*=\s*["']?\s*([a-z0-9._:-]+)""", re.I)


def page_encoding(content: bytes) -> str:
    """Кодировка из `<meta>` в начале страницы; нет её или имя незнакомое — utf-8."""
    found = _META_CHARSET.search(content[:SNIFF_BYTES])
    if found is None:
        return "utf-8"
    return _text_codec(found.group(1).decode("ascii")) or "utf-8"


def _text_codec(name: str) -> str | None:
    """Имя кодека, которым читается текст, — или `None`.

    `codecs.lookup` знает и нетекстовые кодеки — `base64`, `zlib`, `rot13`: на них `.text`
    у httpx падает, а `zlib` к тому же распаковывает. Поэтому имя проверяется пробным
    чтением, а не только поиском.
    """
    try:
        codec = codecs.lookup(name).name
        b"<".decode(codec, "replace")
    except (LookupError, ValueError) as exc:  # ValueError — кодеки `undefined`, `idna`
        logger.debug(
            "charset: %r из <meta> — не кодировка текста (%r), читаем как utf-8", name, exc
        )
        return None
    # `<meta>` прочитан как ASCII — значит, страница не в utf-16/32, что бы она ни писала.
    return "utf-8" if codec.startswith(("utf-16", "utf-32")) else codec
