"""Чекпойнт прогона по файлу: строка JSON на домен, дописывается сразу.

Прогон по нескольким тысячам доменов идёт часами и обрывается. Каждый
домен дописывается строкой немедленно, повторный запуск пропускает
пройденные, а итог собирается из этого файла, а не из памяти процесса.

**Хвост бывает рваным, и это стоит строки, а не прогона.** Процесс убивают
посреди записи, и последняя строка остаётся недописанной — иногда посреди
многобайтного знака: строки пишутся с `ensure_ascii=False`. До 30.09.2026
файл декодировался целиком, и такой обрыв ронял каждое следующее
возобновление с выходом 2; помогал только `--restart`, стиравший весь
прогресс. А следующая запись приклеивалась к обрывку и пропадала вместе
с ним. Поэтому файл читается байтами и каждая строка разбирается отдельно,
а запись после рваного хвоста начинается с новой строки.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


def records(checkpoint: Path) -> Iterator[tuple[str, dict[str, str]]]:
    """Записи чекпойнта по порядку: домен и строка итога.

    Битая строка пропускается и называется в логе номером: её домен, если
    он там был, просто пройдут заново. Молча — нельзя: итог, который короче
    прохода, иначе нечем объяснить.
    """
    if not checkpoint.exists():
        return
    with checkpoint.open("rb") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                # Байты, а не текст: разбор строки декодирует её сам, и обрыв
                # посреди знака стоит этой строки, а не чтения всего файла.
                record = json.loads(raw)
                host, row = str(record["host"]), dict(record["row"])
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "чекпойнт %s: строка %s не разобрана (%r) — пропущена, "
                    "её домен, если он там был, пройдём заново",
                    checkpoint,
                    number,
                    exc,
                )
                continue
            yield host, row


def done_hosts(checkpoint: Path) -> set[str]:
    """Домены, уже записанные в чекпойнт."""
    return {host for host, _ in records(checkpoint)}


def rows_from_checkpoint(checkpoint: Path) -> list[dict[str, str]]:
    """Строки итога из чекпойнта, в порядке прохода."""
    return [row for _, row in records(checkpoint)]


def _torn(path: Path) -> bool:
    """Оборван ли хвост файла: он не пуст и не кончается переводом строки."""
    if not path.exists():
        return False
    with path.open("rb") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            return False
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"


class Checkpoint:
    """Дописывает по строке на домен и сбрасывает на диск сразу.

    Без сброса строки живут в буфере, и обрыв съедает последние сотни
    доменов — то есть именно то, от чего чекпойнт защищает. `flush` отдаёт
    строку системе — этого хватает при убитом процессе; `fsync` — диску,
    и это уже про потерю питания (ревью #126). Цена — один вызов на домен,
    а домен идёт секунды.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        if _torn(path):
            # Обрывок прошлого запуска остаётся битой строкой, но только он:
            # без перевода строки первая же запись этого запуска приклеилась бы
            # к нему и пропала бы вместе с ним.
            logger.warning(
                "чекпойнт %s: хвост оборван прошлым запуском — пишем с новой строки", path
            )
            with path.open("ab") as handle:
                handle.write(b"\n")

    async def add(self, host: str, row: dict[str, str]) -> None:
        line = json.dumps({"host": host, "row": row}, ensure_ascii=False) + "\n"
        async with self._lock:
            with self._path.open("ab") as handle:
                handle.write(line.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
