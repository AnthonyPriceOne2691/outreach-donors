"""Присланный с экрана файл — временной копией: общий читатель CSV берёт путь, а не байты.

Загрузка базы (`intake.read_bytes`) и стоп-лист (`stoplist.read_upload`) кладут байты на диск
одним правилом имени (`file_name`). Имя приходит от клиента как есть: путь, `..`, знаки,
которых нет в именах файлов, и длина за пределом файловой системы — 255 байт у ext4, а буква
кириллицы — два байта, иероглиф — до четырёх. Без правила такое имя роняло запрос ошибкой
диска (`IsADirectoryError`, `ENAMETOOLONG`) — ответом 500 мимо слов отказа.

Имя копии — то же, что назовут отказы читателя («файл … не читается как CSV»): человек узнаёт
в нём свой файл, поэтому буквы и цифры любого языка остаются.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath

#: Знаки вне букв, цифр, «.», «-», «_» и пробела — в «_».
_UNSAFE = re.compile(r"[^\w.\- ]")
#: Предел имени копии в байтах UTF-8 — с запасом до 255 байт имени у ext4: кириллице — сто букв,
#: как было у загрузки базы, иероглифам за основной плоскостью — пятьдесят.
NAME_BYTES = 200


def file_name(name: str, fallback: str) -> str:
    """Имя для диска и для слов отказа: последнее звено пути, без точек в начале (`..` — не имя,
    а каталог выше), чужие знаки — «_», не длиннее `NAME_BYTES` байт — буква на обрезе не
    рвётся. Не осталось ничего — `fallback`."""
    clean = _UNSAFE.sub("_", PurePath(name).name).lstrip(".")
    return clean.encode()[:NAME_BYTES].decode(errors="ignore") or fallback


@contextmanager
def saved(data: bytes, name: str, *, fallback: str) -> Iterator[Path]:
    """Байты — временным файлом с именем по `file_name`; каталог стирается на выходе."""
    with tempfile.TemporaryDirectory(prefix="sales-upload-") as folder:
        path = Path(folder) / file_name(name, fallback)
        path.write_bytes(data)
        yield path
