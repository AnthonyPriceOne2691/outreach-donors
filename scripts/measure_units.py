"""Замер стоимости запросов Ahrefs и доли доменов, проходящих порог DR.

Отвечает на два вопроса, от которых зависит вся смета (см. TZ.md, раздел
«Арифметика»):

1. Сколько юнитов стоит проверка домена поштучно и пакетом.
2. Какая доля сырых доменов выдачи проходит DR >= 20 — от неё зависит объём
   второй, дорогой ступени фильтра.

Запуск:
    .venv/bin/python scripts/measure_units.py --hosts sample.txt

Стоимость самого замера печатается перед выходом: скрипт тратит юниты
и обязан об этом отчитаться.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from dataclasses import dataclass, field

import httpx
from backend.config import ahrefs, filters

BATCH_URL = "/v3/batch-analysis/batch-analysis"
# Минимум на запрос: пачка меньше этого числа строк оплачивается как она.
MIN_REQUEST_UNITS = 50
# Больше в один запрос провайдер не принимает.
MAX_TARGETS_PER_BATCH = 100


@dataclass(slots=True)
class Measurement:
    """Итог одного пакетного запроса."""

    rows: int
    units: int
    unit_per_row: int
    values: list[dict[str, object]] = field(default_factory=list)

    @property
    def units_per_domain(self) -> float:
        return self.units / self.rows if self.rows else 0.0


async def _batch(client: httpx.AsyncClient, hosts: list[str], select: list[str]) -> Measurement:
    payload = {
        "select": select,
        "targets": [{"url": h, "mode": "subdomains", "protocol": "both"} for h in hosts],
    }
    resp = await client.post(BATCH_URL, json=payload)
    resp.raise_for_status()
    rows = next(iter(resp.json().values()))
    return Measurement(
        rows=len(rows),
        units=int(resp.headers.get("x-api-units-cost-total-actual", 0)),
        unit_per_row=int(resp.headers.get("x-api-units-cost-row", 0)),
        values=rows,
    )


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def measure(hosts: list[str]) -> int:
    """Прогоняет просев по DR и печатает распределение. Возвращает расход."""
    spent = 0
    drs: list[float] = []

    headers = {"Authorization": f"Bearer {ahrefs.API_KEY}", "Accept": "application/json"}
    async with httpx.AsyncClient(
        base_url=ahrefs.BASE_URL, headers=headers, timeout=ahrefs.TIMEOUT_S
    ) as client:
        for chunk in _chunks(hosts, MAX_TARGETS_PER_BATCH):
            m = await _batch(client, chunk, ["url", "domain_rating"])
            spent += m.units
            for row in m.values:
                dr = row.get("domain_rating")
                if isinstance(dr, int | float):
                    drs.append(float(dr))
            print(
                f"  пачка {m.rows:>3} доменов → {m.units:>5} юнитов "
                f"({m.units_per_domain:.1f} на домен, строка {m.unit_per_row})"
            )

    if not drs:
        print("Ни по одному домену не вернулся DR — проверь ключ и список.")
        return spent

    drs.sort()
    passed = sum(1 for d in drs if d >= filters.MIN_DR)
    share = passed / len(drs)

    print(f"\nDR получен по {len(drs)} доменам из {len(hosts)}")
    print(f"  минимум {drs[0]:.0f} · медиана {statistics.median(drs):.0f} · максимум {drs[-1]:.0f}")
    print("\nРаспределение:")
    for lo, hi in ((0, 10), (10, 20), (20, 30), (30, 50), (50, 70), (70, 101)):
        n = sum(1 for d in drs if lo <= d < hi)
        print(
            f"  DR {lo:>2}–{hi - 1:<3} {n:>4} ({100 * n / len(drs):>5.1f}%) {'█' * (40 * n // len(drs))}"
        )

    print(f"\nПроходят DR >= {filters.MIN_DR}: {passed} из {len(drs)} = {100 * share:.1f}%")
    _print_projection(share)
    return spent


def _print_projection(pass_share: float) -> None:
    """Во что обходится месяц при измеренной доле прохождения.

    Раскладка ступеней выбрана замером (см. okf/unit-economy.md): просев по DR
    пакетом, затем полный набор метрик выжившим, и только после этого —
    страны, самый дорогой запрос.
    """
    screen_dr = 2  # пакет только с domain_rating, по 100 доменов в запросе
    full_metrics = 18  # пакет со всеми четырьмя порогами
    by_country = 55  # страны с limit=5; без limit тот же запрос стоит 1650
    dr_share = 0.83  # замер: доля, проходящая только по DR

    n = 100_000
    s1 = n * screen_dr
    s2 = int(n * dr_share) * full_metrics
    s3 = int(n * pass_share) * by_country
    total = s1 + s2 + s3

    print("\nПрогноз расхода на просев 100 000 доменов в месяц:")
    print(f"  ступень 1, просев по DR:        {s1:>10,} юнитов".replace(",", " "))
    print(f"  ступень 2, метрики выжившим:    {s2:>10,} юнитов".replace(",", " "))
    print(f"  ступень 3, страны прошедшим:    {s3:>10,} юнитов".replace(",", " "))
    print(f"  итого:                          {total:>10,} юнитов".replace(",", " "))
    print(f"  на домен:                       {total / n:>10.1f} юнитов")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hosts", required=True, help="файл со списком доменов, по одному в строке"
    )
    args = parser.parse_args()

    if not ahrefs.API_KEY:
        print("AHREFS_API_KEY не задан — замер невозможен.")
        return 2

    with open(args.hosts, encoding="utf-8") as fh:
        hosts = [line.strip() for line in fh if line.strip()]

    print(f"Замер на {len(hosts)} доменах.\n")
    spent = asyncio.run(measure(hosts))
    print(f"\nЗамер обошёлся в {spent} юнитов.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
