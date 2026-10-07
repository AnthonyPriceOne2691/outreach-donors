"""Файл с текстами продаж — только вне репозитория, читается целиком или никак.

База знаний и цепочка писем грузятся из файлов, которые собирают люди. Это
коммерческие тексты, а репозиторий публичный: файл внутри копии репозитория
не читается вовсе — одна команда `git add .` унесла бы его в историю навсегда.

Прочитанное сверяется с базой по ключу записи (`sort_against`): новое, такое же,
отличается — и сколько строк базы в файле нет.

Одно место на обе загрузки: правило «вне репозитория», отказы словами и сверка
не должны разойтись между ними на первой правке одной из копий.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class OutsideFileError(ValueError):
    """Файл не читается целиком. Текст — что делать."""


def repository_of(path: Path) -> Path | None:
    """Копия репозитория, внутри которой лежит файл: каталог с `.git` и модулем продаж."""
    for parent in path.resolve().parents:
        if (parent / ".git").exists() and (parent / "backend/features/sales").is_dir():
            return parent
    return None


def read_json(path: Path, *, leaks: str, too_big: str, max_bytes: int) -> Any:
    """Файл → разобранный JSON. Слова отказа: `leaks` — что уехало бы в публичную
    историю, `too_big` — что сказать о файле больше `max_bytes`."""
    if (root := repository_of(path)) is not None:
        raise OutsideFileError(
            f"файл {path} лежит в копии репозитория {root} — {leaks} уехали бы "
            "в публичную историю; положите файл вне репозитория"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OutsideFileError(f"файл {path} не открылся: {exc.strerror or exc}") from exc
    if len(data) > max_bytes:
        raise OutsideFileError(
            f"файл {path.name} больше {max_bytes // 1024 // 1024} МБ — {too_big}"
        )
    try:
        return json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise OutsideFileError(f"файл {path.name} не в UTF-8 (байт {exc.start})") from exc
    except json.JSONDecodeError as exc:
        raise OutsideFileError(
            f"файл {path.name} — не JSON: {exc.msg}, строка {exc.lineno}"
        ) from exc


@dataclass(frozen=True, slots=True)
class Sorting[N, R]:
    """Записи файла против строк базы по ключу: новые, такие же и отличающиеся."""

    added: list[N] = field(default_factory=list)
    same: list[N] = field(default_factory=list)
    differs: list[tuple[N, R]] = field(default_factory=list)
    #: Строк базы, которых нет в файле: загрузка их не трогает.
    absent: int = 0


def sort_against[N, R, K: Hashable](
    news: Sequence[N],
    rows: Mapping[K, R],
    *,
    key: Callable[[N], K],
    same: Callable[[N, R], bool],
) -> Sorting[N, R]:
    """Сверка файла с базой. Ничего не пишет: что делать с отличающимся, решает загрузка."""
    result: Sorting[N, R] = Sorting(absent=len(set(rows) - {key(new) for new in news}))
    for new in news:
        row = rows.get(key(new))
        if row is None:
            result.added.append(new)
        elif same(new, row):
            result.same.append(new)
        else:
            result.differs.append((new, row))
    return result
