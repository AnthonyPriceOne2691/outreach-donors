"""Байты письма в текст — в той кодировке, в которой письмо написано.

Платформа приёма присылает текстовые поля **в исходной кодировке письма**
и отдельным полем `charsets` говорит, в какой именно. Прочитать их как
UTF-8 значит получить «Çäðàâñòâóéòå» вместо «Здравствуйте» у русского
письма в windows-1251 и потерять знак евро у немецкого в windows-1252 —
то есть цену без валюты. Так и было, пока форму разбирал веб-фреймворк:
он о `charsets` не знает и при ошибке молча читает всё как latin-1.

**Названная кодировка — не всегда настоящая.** Почтовые программы
десятилетиями пишут «iso-8859-1», а шлют windows-1252: байт 0x80 у них —
знак евро, а не управляющий символ. Браузеры поэтому читают такие метки
по кодировке-надмножеству, и мы делаем так же (`_SUPERSETS`). Если
надмножество не подошло, пробуем названную как есть — она буквальнее.

**Непрочитанное не выбрасывается и не прячется.** Байт, которого нет
в кодировке, заменяется знаком «�», а сама замена возвращается словами:
текст в кракозябрах на экране объясняется только строкой в логе.
"""

from __future__ import annotations

import codecs
import json
import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Метка → кодировка, которой её читают на деле. Каждая справа — надмножество
#: названной слева: на законном тексте результат тот же, на «незаконном»
#: (0x80 в latin-1, расширенные иероглифы в gb2312) — читаемый вместо «�».
_SUPERSETS: dict[str, str] = {
    "iso-8859-1": "cp1252", "iso8859-1": "cp1252", "latin1": "cp1252",
    "latin-1": "cp1252", "l1": "cp1252", "cp819": "cp1252", "ibm819": "cp1252",
    "iso-8859-9": "cp1254", "latin5": "cp1254",
    "tis-620": "cp874", "iso-8859-11": "cp874",
    "euc-kr": "cp949", "ks_c_5601-1987": "cp949", "ks_c_5601": "cp949",
    "gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "gb_2312-80": "gb18030",
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932",
    "big5": "big5hkscs",
    "iso-8859-8-i": "iso-8859-8",
    "x-mac-cyrillic": "mac-cyrillic",
    "unicode-1-1-utf-7": "utf-7",
}  # fmt: skip

#: Метки «только латиница». Восьмибитный байт под ними — заведомо ошибка
#: метки, и чаще всего за ней стоит UTF-8, а не что-то экзотическое.
_ASCII_LABELS = frozenset({"us-ascii", "ascii", "ansi_x3.4-1968", "iso646-us", "us"})

#: Поля, в которых лежит само письмо. Для них платформа обязана назвать
#: кодировку, и её отсутствие стоит строки в логе.
CONTENT_FIELDS = ("to", "from", "cc", "subject", "text", "html")


def _known(name: str) -> str | None:
    """Имя кодировки, которую Python знает как текстовую, или `None`.

    Метку пишет отправитель письма, то есть кто угодно: «base64» или «zlib»
    Python тоже находит, но текстом они байты не делают.
    """
    try:
        found = codecs.lookup(name).name
        b"".decode(found)
    except LookupError:
        logger.debug("приём: кодировку %r Python текстом не читает", name)
        return None
    return found


def _candidates(label: str | None) -> list[str]:
    """Кодировки, которыми стоит попробовать прочитать байты, по порядку."""
    tag = (label or "").strip().strip("\"'").lower()
    if not tag:
        return []
    if tag in _ASCII_LABELS:
        return ["utf-8", "cp1252"]
    found = [_known(_SUPERSETS.get(tag, tag)), _known(tag)]
    return list(dict.fromkeys(name for name in found if name is not None))


def _replaced(data: bytes, name: str) -> str:
    """Прочитать с заменой непрочитанного. Кодировка, которая не умеет даже
    этого (у Python есть и такие — «undefined»), уступает UTF-8."""
    try:
        return data.decode(name, "replace")
    except (UnicodeError, LookupError) as exc:
        logger.warning("приём: кодировка %s не читает даже с заменой (%s) — беру UTF-8", name, exc)
        return data.decode("utf-8", "replace")


def decode(data: bytes, label: str | None) -> tuple[str, str | None]:
    """Прочитать байты. Вторым значением — что пошло не так, словами, или `None`.

    Нет метки или она незнакома — читаем как UTF-8: письма без названной
    кодировки в подавляющем большинстве именно такие.
    """
    # Короткого пути «одна латиница — значит ASCII» нет намеренно: ISO-2022-JP
    # и UTF-7 целиком семибитные, и японское письмо, прочитанное как ASCII,
    # стало бы строкой escape-последовательностей.
    candidates = _candidates(label)
    unknown = f"кодировка «{label}» незнакома" if label and not candidates else None
    for name in candidates or ["utf-8"]:
        try:
            text = data.decode(name)
        except (UnicodeError, LookupError) as exc:
            # Не последняя попытка: итог, если не подойдёт ни одна, уходит
            # вызывающему словами и в лог — там, где известно, чьё это поле.
            logger.debug("приём: байты не читаются как %s (%s)", name, exc)
            continue
        return text, f"{unknown} — прочитано как UTF-8" if unknown else None

    replaced = " — непрочитанное заменено знаком «�»"
    if candidates:
        return _replaced(data, candidates[0]), f"байты не читаются в «{label}»{replaced}"
    said = unknown or "кодировка не названа"
    return _replaced(data, "utf-8"), f"{said}, а как UTF-8 байты не читаются{replaced}"


def _charsets_of(raw: bytes | None) -> dict[str, str]:
    """Поле `charsets`: «имя поля → кодировка». Непонятное — пусто и строка в логе."""
    if not raw:
        return {}
    try:
        found = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        logger.warning("приём: поле charsets не JSON — %r", raw[:120])
        return {}
    if not isinstance(found, dict):
        logger.warning("приём: поле charsets не словарь — %r", raw[:120])
        return {}
    return {str(key).lower(): str(value) for key, value in found.items() if value}


def decode_fields(fields: Mapping[str, bytes]) -> dict[str, str]:
    """Текстовые поля формы — строками, каждое в своей кодировке.

    Поля, которых нет в `charsets` (заголовки, конверт, список вложений),
    — служебные, их пишет сама платформа, и они в UTF-8.
    """
    charsets = _charsets_of(fields.get("charsets"))
    decoded: dict[str, str] = {}
    problems: list[str] = []
    for name, value in fields.items():
        label = charsets.get(name.lower())
        if label is None and name.lower() not in CONTENT_FIELDS:
            label = "utf-8"
        decoded[name], problem = decode(value, label)
        if problem is not None:
            problems.append(f"«{name}»: {problem}")
    if problems:
        logger.warning("приём: кодировка полей — %s", "; ".join(problems))
    return decoded
