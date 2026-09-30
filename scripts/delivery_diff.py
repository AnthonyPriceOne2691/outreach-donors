#!/usr/bin/env python3
"""Что изменилось: дифф, идентификаторы правки, уроки из архива.

Часть `delivery_check.py` (`delivery@1.53`). Ответ на вопрос «на что вообще
смотреть»: объём диффа, какие имена в нём появились и какие строки архива к нему
применимы. Суждения здесь нет — оно в разделах, которые этим пользуются.
"""

from __future__ import annotations

import re

from delivery_base import out_of_blast_radius, git
from delivery_decisions import DECISION_STOPWORDS

def diff_stats(base: str) -> tuple[int, int, int, int, list[str]] | None:
    """(files, added, deleted, excluded, paths) для base..HEAD; None если ref недоступен.

    `paths` — только код: процессные артефакты отфильтрованы тем же
    `out_of_blast_radius`, потому что оба потребителя (breaker'ы §3.4 и
    сопоставление уроков §2.2a) спрашивают про изменения в коде, а не в его
    описании. Сгенерированное (lock-файлы) не код в том же смысле: его никто не
    пишет и не читает построчно.
    """
    if not git("rev-parse", "--verify", "--quiet", base).strip():
        return None
    merge_base = git("merge-base", base, "HEAD").strip() or base
    files = added = deleted = excluded = 0
    paths: list[str] = []
    for line in git("diff", "--numstat", f"{merge_base}..HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        if out_of_blast_radius(path):
            excluded += 1
            continue
        files += 1
        paths.append(path)
        added += int(a) if a.isdigit() else 0      # "-" у бинарников
        deleted += int(d) if d.isdigit() else 0
    return files, added, deleted, excluded, paths


def archive_index_rows(text: str) -> list[tuple[list[str], list[str]]]:
    """[(префиксы путей, id уроков)] из таблицы A.13.

    Разбор построчный по markdown-таблице: строка шапки и разделитель отсеиваются
    сами — в них нет backtick'ов, а значит и путей. Формат жёсток ровно в двух
    колонках (пути в backticks, id вида L<N>), чтобы правило §2.2a было
    исполнимым, а не пожеланием.
    """
    rows: list[tuple[list[str], list[str]]] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        prefixes = [p for p in re.findall(r"`([^`]+)`", cells[2]) if "/" in p]
        if prefixes:
            rows.append((prefixes, re.findall(r"\bL\d+\b", cells[3])))
    return rows


def applicable_lessons(index_text: str, changed: list[str]) -> list[str]:
    """id уроков из строк, чьи пути пересекаются с диффом (порядок сохранён)."""
    out: list[str] = []
    for prefixes, ids in archive_index_rows(index_text):
        if any(c.startswith(p) for p in prefixes for c in changed):
            out.extend(ids)
    return list(dict.fromkeys(out))



def diff_identifiers(paths: list[str], base: str) -> set[str]:
    """Токены, которые есть в диффе: базовые имена файлов + идентификаторы кода.

    Берём и пути, и содержимое изменённых строк: решение может ссылаться и на файл
    («вынес в sentences.py»), и на функцию («`_cut_points` считает границы»).

    ⚠ `base` — ПАРАМЕТР, и это несущее (`delivery@1.72`, поле). Здесь стояла
    константа `HEAD~1..HEAD`, пока вызывающие уже брали базу из `--diff-base`:
    пути приезжали от диффа всей поставки, а тела строк — от последнего коммита.
    У поставки из двух коммитов (код, потом артефакты) порог §12.5 «≥2 цитаты из
    диффа» становился невыполнимым АРИФМЕТИЧЕСКИ — в последнем коммите кода нет.
    Это `cqg@2.05` («база порога — цель мержа») во втором скрипте: ту правку
    внесли в порог и не провели в извлекатель, который тот же порог и кормит.
    Правило к классу: база сравнения — всегда параметр, и у одной проверки она
    ОДНА; половина, взявшая базу сама, отменяет вторую молча.
    """
    ids: set[str] = set()
    for path in paths:
        for part in re.split(r"[/\\.]", path):
            if len(part) >= 4 and part.lower() not in DECISION_STOPWORDS:
                ids.add(part.lower())
    body = git("diff", "--unified=0", f"{base}..HEAD", "--", *paths) if paths else ""
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", body):
        low = tok.lower()
        if low not in DECISION_STOPWORDS:
            ids.add(low)
    return ids


