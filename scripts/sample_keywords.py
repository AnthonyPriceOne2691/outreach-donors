"""Набрать ключевые слова для прогона из Ahrefs.

Нужен, пока нет доступа к основному источнику выдачи: ключи для проверки
логики приходится добывать самим, а выдумывать их из головы — значит
проверять пайплайн на запросах, которых никто не задаёт.

Отбираются коммерческие запросы: с ненулевой ценой клика и разумным
объёмом. Это не украшение, а суть — по коммерческой выдаче Google уже
провёл отбор, и домены там другие, чем по информационной. Именно на этом
расхождении воронка дала 85% вместо замеренных на сырых доменах 39%
(okf/funnel-calibration.md).

Запрос платный: 22 юнита за строку. Скрипт называет расход перед выходом,
потому что тратит общий с соседней системой ключ.

Запуск:
    .venv/bin/python scripts/sample_keywords.py \
        --seed "best running shoes" --seed "best vpn" \
        --country us --per-seed 40 --out keywords.txt
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from backend.config import ahrefs as cfg

ENDPOINT = "/v3/keywords-explorer/matching-terms"
BASE_URL = "https://api.ahrefs.com"
TIMEOUT = 60.0


@dataclass(slots=True)
class Harvest:
    """Что набрали и во что это обошлось."""

    keywords: list[str] = field(default_factory=list)
    units: int = 0
    skipped: int = 0  # строки, которые не прошли отбор или не разобрались


def _select(rows: list[dict[str, object]], *, min_volume: int, commercial_only: bool) -> Harvest:
    """Отбор строк ответа. Пропущенная строка считается, а не исчезает молча."""
    out = Harvest()
    for row in rows:
        keyword = row.get("keyword")
        volume = row.get("volume")
        cpc = row.get("cpc")

        if not isinstance(keyword, str) or not keyword.strip():
            out.skipped += 1
            continue
        if not isinstance(volume, int) or volume < min_volume:
            out.skipped += 1
            continue
        if commercial_only and not (isinstance(cpc, int) and cpc > 0):
            out.skipped += 1
            continue

        out.keywords.append(keyword.strip())
    return out


def harvest(seeds: list[str], args: argparse.Namespace) -> Harvest:
    total = Harvest()
    headers = {"Authorization": f"Bearer {cfg.API_KEY}"}

    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=TIMEOUT) as client:
        for seed in seeds:
            params = {
                "country": args.country.lower(),
                "keywords": seed,
                "select": "keyword,volume,difficulty,cpc",
                "limit": args.per_seed,
                "order_by": "volume:desc",
                "match_mode": "terms",
            }
            response = client.get(ENDPOINT, params=params)
            total.units += int(response.headers.get("x-api-units-cost-total-actual", 0))

            if response.status_code >= 400:
                print(
                    f"Запрос по «{seed}» отклонён ({response.status_code}): {response.text[:200]}",
                    file=sys.stderr,
                )
                continue

            rows = response.json().get("keywords")
            if not isinstance(rows, list):
                # «Не поняли ответ» обязано быть громким: молча вернув пустоту,
                # мы бы решили, что по запросу ничего нет.
                print(f"Ответ по «{seed}» не разобран: нет списка keywords", file=sys.stderr)
                continue

            picked = _select(rows, min_volume=args.min_volume, commercial_only=not args.any_intent)
            total.keywords.extend(picked.keywords)
            total.skipped += picked.skipped
            print(f"  «{seed}»: взято {len(picked.keywords)}, отброшено {picked.skipped}")

    # Порядок сохраняем, дубли убираем: один ключ дважды — это дважды
    # оплаченная выдача.
    total.keywords = list(dict.fromkeys(total.keywords))
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", required=True, help="затравка, можно несколько")
    parser.add_argument("--country", default="us", help="код страны ISO-2")
    parser.add_argument("--per-seed", type=int, default=40, help="строк на затравку")
    parser.add_argument("--min-volume", type=int, default=200, help="нижний порог частотности")
    parser.add_argument(
        "--any-intent",
        action="store_true",
        help="брать и запросы без цены клика (по умолчанию только коммерческие)",
    )
    parser.add_argument("--out", type=Path, required=True, help="куда положить список")
    args = parser.parse_args(argv)

    if not cfg.API_KEY:
        print("AHREFS_API_KEY пуст — заполнить .env", file=sys.stderr)
        return 2

    print(f"Затравок: {len(args.seed)}, по {args.per_seed} строк на каждую.")
    result = harvest(args.seed, args)

    if not result.keywords:
        print("Ни одного ключа не набрано.", file=sys.stderr)
        print(f"Потрачено юнитов: {result.units}", file=sys.stderr)
        return 1

    args.out.write_text("\n".join(result.keywords) + "\n", encoding="utf-8")
    print(f"\nКлючей: {len(result.keywords)} → {args.out}")
    print(f"Отброшено строк: {result.skipped}")
    print(f"Потрачено юнитов: {result.units}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
