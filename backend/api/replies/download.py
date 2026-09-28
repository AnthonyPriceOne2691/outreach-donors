"""Заголовки ответа, которым отдаётся вложение донора.

Файл пришёл снаружи, и написал его не наш человек (`docs/SECURITY.md`).
Открытый с нашего адреса HTML или SVG из вложения — это чужой скрипт
на странице, где лежит пропуск сотрудника. Поэтому файл отдаётся **только
на скачивание**, и каждый заголовок ниже закрывает свой путь к показу:

* `application/octet-stream` — тип, который браузер не рисует, какой бы
  тип ни назвал отправитель;
* `attachment` — сохранить, а не открыть;
* `nosniff` — не угадывать тип по содержимому;
* `no-store` — не оставлять копию прайса в кэше браузера и прокси.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

#: Символы, которые в запасном имени файла не живут: кавычка и обратная
#: черта ломают заголовок, управляющие — строку заголовка, остальные —
#: путь на диске того, кто сохраняет.
_UNSAFE = re.compile(r'["\\/:*?<>|;\x00-\x1f\x7f]')
_LETTER_OR_DIGIT = re.compile(r"[A-Za-z0-9]")


def _ascii_name(name: str, attachment_id: int) -> str:
    """Имя латиницей — для программ, не понимающих `filename*`.

    «прайс.pdf» латиницей не пишется вовсе: тогда имя собирается из номера
    вложения, а расширение остаётся — по нему система выберет, чем открыть.
    """
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    plain = _UNSAFE.sub("_", plain).strip()
    stem, dot, extension = plain.rpartition(".")
    if _LETTER_OR_DIGIT.search(stem if dot else plain):
        return plain
    tail = f".{extension}" if dot and extension.isalnum() else ""
    return f"attachment-{attachment_id}{tail}"


def download_headers(name: str, attachment_id: int) -> dict[str, str]:
    """Заголовки отдачи вложения: только на скачивание, под настоящим именем."""
    return {
        "Content-Disposition": (
            f'attachment; filename="{_ascii_name(name, attachment_id)}"; '
            f"filename*=UTF-8''{quote(name, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
