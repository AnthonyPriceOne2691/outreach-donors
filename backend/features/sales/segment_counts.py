"""Числа отчёта прогона сбора за все проходы: счётчики — суммой, остальное — последнего прохода.

Своим модулем: их сводят и проход прогона (`segment_run`), и платный поиск повтора по кускам
(`segment_paid`), а второй не должен знать первого.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Числа поиска адреса, которые копятся; остальное (`pending`, заметки) — последнего прохода.
CONTACTS_SUMMED = ("walked", "saved")


def number(value: object) -> int:
    """Число из отчёта; не число (поле правили руками, его нет) — ноль, а не отказ."""
    return int(value) if isinstance(value, int | float) else 0


def counts(*passes: object) -> dict[str, int]:
    """Счётчики проходов по ключам — суммой: ключ одного прохода — как есть."""
    found: dict[str, int] = {}
    for counted in passes:
        if isinstance(counted, Mapping):
            for key, value in counted.items():
                found[str(key)] = found.get(str(key), 0) + number(value)
    return found


def contacts_report(before: Mapping[str, Any] | None, now: Mapping[str, Any]) -> dict[str, Any]:
    """Итог поиска адреса за все проходы: пройдено, найдено и ступени (`counters` — в них
    запросы платного сервиса) — суммой; ждали и заметки — последнего прохода."""
    earlier = dict(before or {})
    return {
        **earlier,
        **now,
        **{key: number(earlier.get(key)) + number(now.get(key)) for key in CONTACTS_SUMMED},
        "counters": counts(earlier.get("counters"), now.get("counters")),
    }
