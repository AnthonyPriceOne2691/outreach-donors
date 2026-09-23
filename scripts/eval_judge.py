"""Оценка судьи площадки на эталонном наборе: кого отрезал, кого пропустил.

Запуск: `python scripts/eval_judge.py [файл.jsonl] [--gate 0.05]`.
По умолчанию — `scripts/data/judge_golden.jsonl`: синтетические сайты из
разных ниш и стран пяти классов — продаёт размещение у себя, издание или
обзорщик, бренд, бренд со страницей для авторов, посредник. Настоящих
доменов в репозитории нет намеренно — он публичный; хосты выдуманы.

**Ворота для правки промпта.** Любая правка `SYSTEM` или `ARBITER_SYSTEM`
в `donors/publisher_judge.py` прогоняется здесь до слияния. Путь тот же,
что в прогоне: модель по выдаче → главная (из файла, без сети) → дверь для
авторов. Главной нет в случае — значит, «не открылась».

**Опасная ошибка — отдельным числом.** Их две, и обе дороже взгляда
человека: донор отрезан (потерян молча) и бренд или посредник принят
(письмо уйдёт тому, кто размещение не продаёт или перепродаёт). «Посмотри»
опасным не считается — это минута человека, а не ошибка в базе.

Отдельные ворота — **ни одного отрезанного продавца размещения**: ради
этого класса судья v2 и заведён.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from backend.config import llm as llm_cfg
from backend.features.donors.author_door import open_door
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.judging import DISPUTED, door_of, settle
from backend.features.donors.publisher_judge import (
    PROMPT_VERSION,
    Decider,
    Judgement,
    Recommendation,
    judge_host,
)
from backend.features.runs.planning import SerpText

DEFAULT = Path(__file__).parent / "data" / "judge_golden.jsonl"

#: Класс сайта → какой совет верен и какой опасен.
WANT: dict[str, Recommendation] = {
    "placement": Recommendation.ACCEPT,
    "publisher": Recommendation.ACCEPT,
    "brand_door": Recommendation.REVIEW,
    "brand": Recommendation.REJECT,
    "vendor": Recommendation.REJECT,
}
DANGER: dict[str, Recommendation] = {
    "placement": Recommendation.REJECT,
    "publisher": Recommendation.REJECT,
    "brand_door": Recommendation.REJECT,
    "brand": Recommendation.ACCEPT,
    "vendor": Recommendation.ACCEPT,
}


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def home_of(case: dict[str, Any]) -> HomeSignals | None:
    raw = case.get("home")
    if not raw:
        return None
    return HomeSignals(
        reached=True,
        shop=tuple(raw.get("shop", ())),
        service=tuple(raw.get("service", ())),
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        nav=tuple(raw.get("nav", ())),
    )


async def judge_case(http: httpx.AsyncClient, case: dict[str, Any]) -> Judgement:
    """Тот же путь, что `judging.judge_candidates`, но главная — из файла."""
    text = SerpText(url=case["url"], title=case["title"], description=case["description"])
    verdict = await judge_host(
        http, host=case["host"], title=text.title, description=text.description
    )
    home = home_of(case)
    if home is not None and verdict.decided_by is not Decider.RULE and verdict.intent in DISPUTED:
        verdict = await settle(http, host=case["host"], text=text, verdict=verdict, home=home)
    return open_door(verdict, door_of(text, home))


async def run(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gate = asyncio.Semaphore(6)

    async with httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http:

        async def one(case: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                verdict = await judge_case(http, case)
            expect = case["expect"]
            return {
                "id": case["id"],
                "expect": expect,
                "verdict": verdict,
                "right": verdict.recommendation is WANT[expect],
                "dangerous": verdict.recommendation is DANGER[expect],
            }

        return list(await asyncio.gather(*(one(case) for case in cases)))


def report(results: list[dict[str, Any]]) -> tuple[float, int]:
    total = len(results)
    tokens = sum(r["verdict"].tokens for r in results)
    dangerous = [r for r in results if r["dangerous"]]
    placement_cut = sum(1 for r in dangerous if r["expect"] == "placement")

    print(f"Версия промпта: {PROMPT_VERSION}, случаев {total}, токенов {tokens}\n")
    print(f"{'класс':<12} {'верно':>7} {'принят':>7} {'посмотри':>9} {'отрезан':>8}")
    for expect in WANT:
        group = [r for r in results if r["expect"] == expect]
        if not group:
            continue
        recs = Counter(r["verdict"].recommendation for r in group)
        right = sum(1 for r in group if r["right"])
        print(
            f"{expect:<12} {right:>3}/{len(group):<3} {recs[Recommendation.ACCEPT]:>7} "
            f"{recs[Recommendation.REVIEW]:>9} {recs[Recommendation.REJECT]:>8}"
        )
    intents = Counter((r["expect"], r["verdict"].intent.value) for r in results)
    print(
        "\nВид по классам: " + ", ".join(f"{e}→{i}: {n}" for (e, i), n in sorted(intents.items()))
    )
    print(f"\nОПАСНЫХ (донор отрезан / бренд или посредник принят): {len(dangerous)}/{total}")
    print(f"Продавцов размещения отрезано: {placement_cut}")

    for r in sorted(results, key=lambda r: (not r["dangerous"], r["id"])):
        if r["right"]:
            continue
        v = r["verdict"]
        print(
            f"  {'!!' if r['dangerous'] else '  '} {r['id']:<18} {r['expect']:<11} "
            f"{v.recommendation.value:<7} {v.intent.value:<16} {v.decided_by.value:<8} "
            f"{v.reason[:90]}"
        )
    return (len(dangerous) / total if total else 0.0), placement_cut


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(DEFAULT))
    parser.add_argument("--gate", type=float, default=0.05, help="допустимая доля опасных ошибок")
    args = parser.parse_args(argv)
    share, placement_cut = report(asyncio.run(run(load(Path(args.path)))))
    if placement_cut:
        print(f"\nВОРОТА ЗАКРЫТЫ: отрезано продавцов размещения {placement_cut}, допуск 0")
        return 1
    if share > args.gate:
        print(f"\nВОРОТА ЗАКРЫТЫ: опасных {share:.0%} при допуске {args.gate:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
