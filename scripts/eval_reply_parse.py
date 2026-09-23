"""Оценка разбора ответов на эталонном наборе: точность по каждому полю.

Запуск: `python scripts/eval_reply_parse.py [файл.jsonl] [--gate 0.05]`.
По умолчанию — `scripts/data/reply_parse_golden.jsonl`: синтетические
ответы по видам случаев, найденных в переписке (две цены, надбавка за
нишу, диапазон, «от», оплата после публикации, отказ, «бесплатно» и т. д.).
Настоящих писем в репозитории нет намеренно — он публичный.

**Ворота для правки промпта.** Любая правка `SYSTEM` в `replies/extract.py`
прогоняется здесь до слияния. Приём взят у соседней системы: там новая
версия промпта шла в работу только после прогона по настоящим письмам.
Отличие — здесь считается точность по полям, а не читается вывод глазами.

**Опасная ошибка — отдельным числом.** Это цена, которая легла бы в базу
сама, без человека, и при этом неверна. Требование ограничивает ошибки
разбора пятью процентами, и считать их надо именно так: ошибка, ушедшая
человеку, стоит минуты, а ушедшая в базу — неверной цены у донора.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.features.core.domain import ReplyKind
from backend.features.replies.extract import PROMPT_VERSION, ExtractClient, Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.outcome import Consequences, decide

DEFAULT = Path(__file__).parent / "data" / "reply_parse_golden.jsonl"
FIELDS = ("placement", "price_white", "price_grey", "currency")


def _money(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


def decision_of(consequences: Consequences) -> str:
    if consequences.store_declines:
        return "declines"
    if consequences.store_free:
        return "free"
    if consequences.store_price:
        return "auto"
    return "review"


def compare(expect: dict[str, Any], found: Extracted) -> list[str]:
    got = {
        "placement": found.placement,
        "price_white": found.price_white,
        "price_grey": found.price_grey,
        "currency": found.currency,
    }
    want = {
        "placement": expect["placement"],
        "price_white": _money(expect["price_white"]),
        "price_grey": _money(expect["price_grey"]),
        "currency": expect["currency"],
    }
    return [name for name in FIELDS if got[name] != want[name]]


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def run(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    client = ExtractClient()
    results: list[dict[str, Any]] = []
    tokens = 0
    for case in cases:
        found = await client.extract(
            Incoming(
                message_id=f"<{case['id']}@golden>",
                to=("donors@ours.test",),
                from_email="editor@donor.test",
                subject="Re: Advertising rates",
                text=case["text"],
            )
        )
        tokens += found.tokens_spent
        decision = decision_of(decide(ReplyKind.HUMAN, found))
        wrong = compare(case["expect"], found)
        # Опасна только цена, которая легла бы в базу сама и при этом неверна.
        dangerous = decision == "auto" and bool(
            {"price_white", "price_grey", "currency"} & set(wrong)
        )
        results.append(
            {
                "id": case["id"],
                "wrong": wrong,
                "decision": decision,
                "want_decision": case.get("decision"),
                "dangerous": dangerous,
                "found": found,
            }
        )
    return results, tokens


def report(results: list[dict[str, Any]], tokens: int) -> float:
    total = len(results)
    misses = Counter(name for r in results for name in r["wrong"])
    exact = sum(1 for r in results if not r["wrong"])
    dangerous = [r for r in results if r["dangerous"]]
    decisions = Counter(r["decision"] for r in results)
    decision_miss = [
        r for r in results if r["want_decision"] and r["want_decision"] != r["decision"]
    ]

    print(f"Версия промпта: {PROMPT_VERSION}, случаев {total}, токенов {tokens}\n")
    print(f"Все поля верно:        {exact}/{total}")
    for name in FIELDS:
        print(f"  {name:<20} {total - misses[name]}/{total}")
    print(f"\nРешения: {dict(decisions)}")
    print(f"Решение не то, что ждали: {len(decision_miss)}")
    print(f"ОПАСНЫХ (неверная цена легла бы сама): {len(dangerous)}/{total}")

    for r in results:
        if not r["wrong"] and not (r["want_decision"] and r["want_decision"] != r["decision"]):
            continue
        f = r["found"]
        print(
            f"  {'!!' if r['dangerous'] else '  '} {r['id']:<18} {r['decision']:<8} "
            f"поля: {','.join(r['wrong']) or '—'} | модель: {f.placement}, "
            f"белая={f.price_white}, серая={f.price_grey}, {f.currency}, "
            f"уверенность {f.confidence:.2f}"
        )
    return len(dangerous) / total if total else 0.0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(DEFAULT))
    parser.add_argument("--gate", type=float, default=0.05, help="допустимая доля опасных ошибок")
    args = parser.parse_args(argv)
    results, tokens = asyncio.run(run(load(Path(args.path))))
    share = report(results, tokens)
    if share > args.gate:
        print(f"\nВОРОТА ЗАКРЫТЫ: опасных {share:.0%} при допуске {args.gate:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
