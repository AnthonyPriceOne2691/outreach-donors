#!/usr/bin/env python3
"""Общее для всех частей доктора: вердикты, палитра, запуск подпроцесса.

Доктор разрезан на модули (`cqg@1.82`): он вырос до 1082 строк при планке 300
(Delivery §9.1a п.5), и рос быстрее, чем проверяемое им, — это назвал полевой
отчёт раньше, чем ратчет веса. Здесь то, что нужно ВСЕМ частям, и только оно:
модуль не импортирует соседей, поэтому цикла быть не может по построению.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# --- вердикты ----------------------------------------------------------------
AUTO = "AUTO"      # проверено исполнением: канарейка покраснела
WEAK = "WEAK"      # работает, но не судит; причина названа самим гейтом
DEAD = "DEAD"      # объявлен и вписан — и молчит на своей канарейке. Только это ложь
ABSENT = "ABSENT"  # не развёрнут
SKIP = "SKIP"      # доктор не умеет это пробовать — назвать, а не умолчать
TOOL = "TOOL"      # это не гейт, а измерительный инструмент: судить его нечем

ORDER = {DEAD: 0, SKIP: 1, WEAK: 2, ABSENT: 3, TOOL: 4, AUTO: 5}
COLOR = {AUTO: "\033[32m", WEAK: "\033[33m", DEAD: "\033[31m",
         ABSENT: "\033[90m", SKIP: "\033[36m", TOOL: "\033[90m"}
RESET = "\033[0m"

NOT_GATES = {"check_gate_value.sh", "assert_digest.sh", "contour_doctor.py"}

SKIP_WORDS = ("пропущен", "не судит", "не проверен", "нет каталога", "ПРОПУЩЕНА",
              "WARNING", "не найден", "недоступен", "не смог")
SUCCESS_WORDS = (": OK", "OK —", "OK -")


def _block_after(text: str, marker: str, lang: str) -> str | None:
    """Первый блок ```lang после marker — с учётом ВЛОЖЕННЫХ фенсов.

    Наивная регулярка обрывается на первом же вложенном фенсе (шаблоны канона их
    содержат), и сравнение тел давало бы вечное «разошлось». Тот же построчный
    сканер, что blocks() в stack_selftest и block_after в сьюте канона.
    """
    if marker not in text:
        return None
    out, depth, started = [], 0, False
    for line in text.split(marker, 1)[1].splitlines():
        if line.startswith("```"):
            info = line[3:].strip().lower()
            if not started:
                if info == lang:
                    started, depth = True, 1
                continue
            if info:
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return "\n".join(out)
            out.append(line)
            continue
        if started:
            out.append(line)
    return None


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    e = {**os.environ, **(env or {})}
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=180, env=e)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, f"{exc}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


