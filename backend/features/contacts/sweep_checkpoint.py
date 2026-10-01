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

**Домен может встречаться не раз, и верна последняя запись.** Исход
«повторить» (сайт не ответил, закрылся, обход оборван) пишется в файл,
чтобы итог его показывал, но пройденным домен не делает: следующий
запуск идёт по нему снова и дописывает новую запись.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

#: Статус строки итога, по которой домен ещё не пройден.
RETRY = "retry"

#: Сколько раз домен проходят с исходом «повторить», прежде чем сдаться.
#: Предела не было, и сайт, который не отвечает никогда, шёл заново на каждом
#: возобновлении — вечно (ревью #126). Три — та же граница, что у поиска
#: контактов донорам.
MAX_ATTEMPTS = 3

#: Попытки кончились: домен пройден, но не проверен — это не «адреса нет».
#: Причина последнего отказа остаётся в `retry_reason`.
UNREACHABLE = "unreachable"


#: Битые строки, уже названные предупреждением в этом процессе: (файл, номер).
_NAMED: set[tuple[str, int]] = set()

_BROKEN_LINE = (
    "чекпойнт %s: строка %s не разобрана (%r) — пропущена, "
    "её домен, если он там был, пройдём заново"
)


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
                # Чекпойнт читают несколько раз за запуск (пройденные, повторы,
                # итог), и одна битая строка звучала бы предупреждением на
                # каждое чтение (ревью #127). Предупреждение — один раз на
                # строку, повторы — уровнем ниже: молчать гейт не даёт и не надо.
                key = (str(checkpoint), number)
                if key in _NAMED:
                    logger.debug(_BROKEN_LINE, checkpoint, number, exc)
                else:
                    _NAMED.add(key)
                    logger.warning(_BROKEN_LINE, checkpoint, number, exc)
                continue
            yield host, row


def latest(checkpoint: Path) -> dict[str, dict[str, str]]:
    """Последняя запись по каждому домену, в порядке первого появления."""
    rows: dict[str, dict[str, str]] = {}
    for host, row in records(checkpoint):
        rows[host] = row
    return rows


def done_hosts(checkpoint: Path) -> set[str]:
    """Домены, пройденные окончательно. «Повторить» сюда не входит."""
    return {host for host, row in latest(checkpoint).items() if row.get("status") != RETRY}


def retry_hosts(checkpoint: Path) -> set[str]:
    """Домены, которые прошлый запуск оставил «повторить»."""
    return {host for host, row in latest(checkpoint).items() if row.get("status") == RETRY}


def retry_counts(checkpoint: Path) -> dict[str, int]:
    """Сколько раз каждый домен уже записан «повторить»."""
    counts: dict[str, int] = {}
    for host, row in records(checkpoint):
        if row.get("status") == RETRY:
            counts[host] = counts.get(host, 0) + 1
    return counts


def rows_from_checkpoint(checkpoint: Path) -> list[dict[str, str]]:
    """Строки итога из чекпойнта: по домену одна, последняя, в порядке прохода."""
    return list(latest(checkpoint).values())


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
        self._retries = retry_counts(path)
        if _torn(path):
            # Обрывок прошлого запуска остаётся битой строкой, но только он:
            # без перевода строки первая же запись этого запуска приклеилась бы
            # к нему и пропала бы вместе с ним.
            logger.warning(
                "чекпойнт %s: хвост оборван прошлым запуском — пишем с новой строки", path
            )
            with path.open("ab") as handle:
                handle.write(b"\n")

    async def add(self, host: str, row: dict[str, str]) -> dict[str, str]:
        """Дописать строку домена; вернуть то, что записано.

        «Повторить» в последний разрешённый раз записывается уже как
        `UNREACHABLE`: решение — в момент записи, чтобы чекпойнт, итог CSV
        и отчёт прохода говорили одно и то же.
        """
        row = self._settled(host, row)
        line = json.dumps({"host": host, "row": row}, ensure_ascii=False) + "\n"
        async with self._lock:
            with self._path.open("ab") as handle:
                handle.write(line.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        return row

    def _settled(self, host: str, row: dict[str, str]) -> dict[str, str]:
        if row.get("status") != RETRY:
            return row
        tries = self._retries.get(host, 0) + 1
        self._retries[host] = tries
        if tries < MAX_ATTEMPTS:
            return row
        reason = row.get("retry_reason", "")
        return {
            **row,
            "status": UNREACHABLE,
            "retry_reason": f"сдались после {tries} попыток: {reason}",
        }
