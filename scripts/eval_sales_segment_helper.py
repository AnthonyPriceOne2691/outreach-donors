"""Оценка помощника описания сегмента: судья по черновику как есть и по описанию помощника.

Запуск:

    python scripts/eval_sales_segment_helper.py                     # черновики × синтетика судьи
    python scripts/eval_sales_segment_helper.py --golden --draft-file old.txt   # живой набор судьи
    python scripts/eval_sales_segment_helper.py --spoil who         # порча: снято «кто подходит»
    python scripts/eval_sales_segment_helper.py --spoil not-fit     # порча: снят список «не подходят»
    python scripts/eval_sales_segment_helper.py --spoil language    # порча: снято правило о языке
    python scripts/eval_sales_segment_helper.py --spoil topic-inverted   # порча: правило наоборот
    python scripts/eval_sales_segment_helper.py --effort medium     # другое усилие помощника — замер
    python scripts/eval_sales_segment_helper.py --out run.json      # итог ещё и файлом

**Что меряется.** Помощник переписывает черновик описания для судьи (`sales/segment_helper.py`),
и польза его — в решениях судьи по этому описанию. Черновик
(`scripts/data/sales_segment_helper_drafts.jsonl`) привязан к гипотезе синтетики судьи
(`sales_segment_synthetic.jsonl`) и говорит тот же замысел иначе: темой («сайты о …»), целью
человека («проверить рассылку по …, у нас есть кейсы»), на другом языке — или строго (контроль:
помощник не портит готовое). Сайты гипотезы судятся дважды — по черновику как есть и по
описанию помощника, разметка сайтов та же. Ещё меряется вывод помощника «черновик расплывчатый»
(он назвал, что в черновике собьёт судью, — список не пуст) против разметки черновика.

**Ловушки:** `letters-language` — в черновике язык писем («пишем им по-русски»): помощник не
должен приписать сайту язык (опыт 15.10), иначе судья режет подходящие сайты другого языка —
ложные «отказ»; `control` — сегмент сам просит справочники или площадки: помощник не должен
вписать их в «не подходят».

**Ворота — предложение, решает владелец:** судья по описаниям помощника — в воротах судьи
(`eval_sales_segment.Gates`: ложных «принять» ≤ 5 %, ложных «отказ» ≤ 10 %, полнота ≥ 70 %);
вывод «расплывчатый» верен не меньше чем на 90 % размеченных черновиков; помощник ответил на
каждый черновик. Числа судьи по черновикам как есть — рядом для сравнения, ворот у них нет.

**Живой набор** (`--golden`) — тот же, что у судьи (манифест, вне репозитория): черновик —
файлом (`--draft-file`, например прежнее описание гипотезы); помощник переписывает его один раз
на гипотезу, и сайты набора судятся по черновику и по описанию помощника.

**Порча** (`--spoil`) снимает строку промпта помощника (`RULES`): `who` — «кто подходит: бизнес,
который сам делает своё», `not-fit` — список «не подходят», `language` — правило о языке сайта;
`topic-inverted` — подменяет правило «сайты о X просят бизнесы X» обратным (`INVERTED`): это
поломка, случившаяся при докрутке 16.10, и ворота обязаны закрыться. Итог словами: поймана ли
порча. Снятие одного правила ворот может и не закрыть — правила промпта и правило судьи v2
перекрывают друг друга; тогда итог так и скажет: «ПОРЧА НЕ ПОЙМАНА».

Модель зовётся по-настоящему — нужен `LLM_API_KEY`; расход в журнал не пишется (как у eval
судьи). Механика — `tests/test_sales_segment_helper_eval.py` на подставной модели.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.config import sales as sales_cfg
from backend.features.sales import segment_helper as sh
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_helper import HelperClient
from backend.features.sales.segment_judge import SegmentClient
from scripts import eval_sales_segment as ev

DRAFTS = ev.DATA / "sales_segment_helper_drafts.jsonl"

#: Порча: начало строки промпта помощника, которую снимают — или подменяют (`INVERTED`).
RULES = {
    "who": "1. Who fits and what the business does itself",
    "not-fit": "3. Always end with what does NOT fit",
    "language": "- The language of the sites:",
    "topic-inverted": "A draft about a topic",
}
RULE_WORDS = {
    "who": "кто подходит — бизнес, который сам делает своё",
    "not-fit": "что не подходит",
    "language": "язык сайта не приписывать",
    "topic-inverted": "«сайты о X» просят бизнесы X",
}
#: Порча подменой, а не снятием: правило наоборот — та поломка, что случилась при докрутке
#: промпта 16.10 (абзац о сегменте справочников модель прочла как «сайты о X» = справочники и
#: СМИ о X — ложных «принять» по описаниям помощника 23 %). Снятие одного правила ворот может и
#: не закрыть: правила промпта перекрывают друг друга.
INVERTED = {
    "topic-inverted": (
        'A draft about a topic ("sites about X", "sites related to X") asks for every site about X: '
        "directories, marketplaces, media and reviews about X fit too — take them out of the "
        "not-fit list."
    ),
}
#: Ловушки черновиков — печатаются отдельной строкой.
TRAPS = ("letters-language", "control")

#: Вывод «расплывчатый» верен не меньше чем на этой доле размеченных черновиков — предложение.
LOOSE_RIGHT_MIN = 0.9
#: Черновиков разом: судья по каждому — сайты гипотезы дважды.
PARALLEL = 4


@dataclass(frozen=True, slots=True)
class Drafted:
    """Черновик, что сделал с ним помощник, и решения судьи по черновику и по ответу помощника."""

    draft_id: str
    hypothesis: str
    draft: str
    loose_expected: bool | None
    trap: str | None
    description: str | None
    unclear: tuple[str, ...]
    problems: tuple[str, ...]
    loose: bool | None
    failure: str | None
    tokens: int
    as_is: tuple[ev.Result, ...]
    helped: tuple[ev.Result, ...]

    @property
    def loose_right(self) -> bool | None:
        """Верен ли вывод «расплывчатый»; `None` — черновик не размечен или ответа нет."""
        if self.loose_expected is None or self.loose is None:
            return None
        return self.loose is self.loose_expected


# --- наборы ---------------------------------------------------------------------------------


def _checked(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("id", "hypothesis", "draft"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ev.SetError(f"{row.get('id')}: нет поля {key}")
    if not isinstance(row.get("loose"), bool):
        raise ev.SetError(f"{row['id']}: не размечен — loose: true | false")
    if row.get("trap") not in (None, *TRAPS):
        raise ev.SetError(f"{row['id']}: ловушка не из списка — {' | '.join(TRAPS)}")
    return row


def drafts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ev.SetError(f"черновиков нет: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_checked(json.loads(line)) for line in lines if line.strip()]


def golden_rows(draft_file: Path | None, cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Живой набор: один черновик файлом на каждую гипотезу набора, без разметки «расплывчатый»."""
    if draft_file is None or not draft_file.is_file():
        raise ev.SetError("живому набору нужен черновик файлом: --draft-file")
    text = draft_file.read_text(encoding="utf-8").strip()
    if not text:
        raise ev.SetError(f"черновик пуст: {draft_file}")
    hypotheses = sorted({case["hypothesis"] for case in cases})
    return [{"id": f"golden-{name}", "hypothesis": name, "draft": text} for name in hypotheses]


def cases_of(cases: Iterable[dict[str, Any]], hypothesis: str) -> list[dict[str, Any]]:
    found = [case for case in cases if case["hypothesis"] == hypothesis]
    if not found:
        raise ev.SetError(f"гипотезы «{hypothesis}» нет в наборе судьи")
    return found


def with_segment(cases: Iterable[dict[str, Any]], segment: str) -> list[dict[str, Any]]:
    """Те же сайты и разметка — другое описание сегмента."""
    return [{**case, "segment": segment} for case in cases]


def spoiled(prompt: str, rule: str) -> str:
    """Промпт помощника без строки правила `rule` (`RULES`) — или с правилом наоборот
    (`INVERTED`): порча для обратного прогона."""
    lines = prompt.splitlines()
    hit = [line.startswith(RULES[rule]) for line in lines]
    if not any(hit):
        raise ev.SetError(f"в промпте помощника нет правила «{RULE_WORDS[rule]}» — портить нечего")
    swap = INVERTED.get(rule)
    kept = [swap if found else line for line, found in zip(lines, hit, strict=True)]
    return "\n".join(line for line in kept if line is not None)


# --- прогон ------------------------------------------------------------------------------------


async def drafted(
    row: dict[str, Any],
    cases: list[dict[str, Any]],
    helper: HelperClient,
    judge: SegmentClient,
    *,
    system: str | None = None,
) -> Drafted:
    """Один черновик: помощник, затем судья по черновику и по ответу помощника."""
    own = cases_of(cases, row["hypothesis"])
    answer = await helper.suggest(row["draft"], system=system)
    as_is = await ev.run(with_segment(own, row["draft"]), judge)
    found = answer.suggestion
    helped = [] if found is None else await ev.run(with_segment(own, found.description), judge)
    return Drafted(
        row["id"],
        row["hypothesis"],
        row["draft"],
        row.get("loose"),
        row.get("trap"),
        None if found is None else found.description,
        () if found is None else found.unclear,
        () if found is None else found.problems,
        None if found is None else found.loose,
        answer.reason,
        answer.tokens,
        tuple(as_is),
        tuple(helped),
    )


async def run(
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    helper: HelperClient,
    judge: SegmentClient,
    *,
    system: str | None = None,
) -> list[Drafted]:
    """Все черновики — по `PARALLEL` разом, итог — в порядке черновиков."""
    gate = asyncio.Semaphore(PARALLEL)

    async def one(row: dict[str, Any]) -> Drafted:
        async with gate:
            return await drafted(row, cases, helper, judge, system=system)

    return list(await asyncio.gather(*(one(row) for row in rows)))


# --- итог --------------------------------------------------------------------------------------


def _counts(results: Iterable[ev.Result]) -> str:
    rows = list(results)
    misfits = [r for r in rows if "yes" not in r.answers]
    fits = [r for r in rows if "yes" in r.answers]
    accepted = sum(r.false_accept for r in misfits)
    rejected = sum(r.false_reject for r in fits)
    to_human = sum(r.lane == "review" for r in rows)
    return (
        f"ложных «принять» {accepted} из {len(misfits)}, ложных «отказ» {rejected} из "
        f"{len(fits)}, человеку {to_human} из {len(rows)}"
    )


def _drafted(d: Drafted) -> None:
    trap = f" · ловушка {d.trap}" if d.trap else ""
    print(f"\n[{d.draft_id}] гипотеза {d.hypothesis}{trap}")
    print(f"  черновик: {d.draft}")
    if d.failure is not None:
        print(f"  ПОМОЩНИК НЕ ОТВЕТИЛ: {d.failure}")
        return
    mark = "" if d.loose_right is not False else "  ← НЕ ТАК"
    print(f"  расплывчатый: {d.loose} (разметка {d.loose_expected}){mark}")
    if d.problems:
        print(f"  что собьёт судью: {' | '.join(d.problems)}")
    print(f"  описание помощника: {d.description}")
    if d.unclear:
        print(f"  вопросы: {' | '.join(d.unclear)}")
    print(f"  судья по черновику: {_counts(d.as_is)}")
    print(f"  судья по описанию помощника: {_counts(d.helped)}")


def _judged(done: Iterable[Drafted], *, helped: bool) -> list[ev.Result]:
    """Решения судьи по всем черновикам: по описаниям помощника — или по черновикам как есть."""
    return [r for d in done for r in (d.helped if helped else d.as_is)]


def _traps(done: list[Drafted]) -> None:
    for trap in TRAPS:
        if caught := [d for d in done if d.trap == trap]:
            print(f"Ловушка {trap}: {_counts(_judged(caught, helped=True))}")


def _silent(done: list[Drafted]) -> list[str]:
    silent = [d.draft_id for d in done if d.failure is not None]
    return [f"помощник не ответил на черновики: {', '.join(silent)}"] if silent else []


def _loose(done: list[Drafted]) -> list[str]:
    """Ворота вывода «расплывчатый» — по размеченным черновикам, на которые помощник ответил."""
    marked = [d for d in done if d.loose_right is not None]
    if not marked:
        return []
    share = sum(bool(d.loose_right) for d in marked) / len(marked)
    print(f"Вывод «расплывчатый» верен: {round(share * len(marked))} из {len(marked)}")
    if share >= LOOSE_RIGHT_MIN:
        return []
    return [f"вывод «расплывчатый» верен на {share:.0%} < {LOOSE_RIGHT_MIN:.0%}"]


def report(done: list[Drafted], gates: ev.Gates) -> list[str]:
    """Печать итога по черновикам и всего. Возвращает нарушенные ворота словами."""
    print(
        f"Промпт помощника: {sh.PROMPT_VERSION}, судьи: {sj.PROMPT_VERSION}; "
        f"черновиков {len(done)}, токенов помощника {sum(d.tokens for d in done)}"
    )
    for d in done:
        _drafted(d)
    helped = _judged(done, helped=True)
    print(f"\nВсего, судья по черновикам как есть: {_counts(_judged(done, helped=False))}")
    print(f"Всего, судья по описаниям помощника: {_counts(helped)}")
    total = ev.metrics(helped)
    print(f"Числа ворот по описаниям помощника: {total}")
    _traps(done)
    failed = ev.closed(total, gates) if helped else ["судье нечего судить — помощник не ответил"]
    return failed + _silent(done) + _loose(done)


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(DRAFTS), help="черновики JSONL")
    parser.add_argument("--cases", default=str(ev.SYNTHETIC), help="набор судьи JSONL")
    parser.add_argument("--golden", action="store_true", help="живой набор судьи по манифесту")
    parser.add_argument("--draft-file", type=Path, default=None, help="черновик живого набора")
    parser.add_argument("--spoil", choices=sorted(RULES), default=None)
    parser.add_argument("--model", default=None, help="модель вместо пина LLM_SALES_SEGMENT_MODEL")
    parser.add_argument(
        "--effort", choices=ev.EFFORTS, default=None, help="усилие помощника вместо продуктового"
    )
    parser.add_argument("--out", type=Path, default=None, help="итог файлом JSON")
    return parser.parse_args(argv)


def _sets(
    args: argparse.Namespace, golden_dir: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if args.golden:
        cases = ev.golden(sales_cfg.SEGMENT_GOLDEN_DIR if golden_dir is None else golden_dir)
        return golden_rows(args.draft_file, cases), cases
    rows, cases = drafts(Path(args.path)), ev.load(Path(args.cases))
    for row in rows:
        cases_of(cases, row["hypothesis"])
    return rows, cases


async def _evaluate(
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    args: argparse.Namespace,
    system: str | None,
) -> list[Drafted]:
    helper, judge = HelperClient(model=args.model), SegmentClient(model=args.model)
    try:
        return await run(rows, cases, helper, judge, system=system)
    finally:
        await helper.aclose()
        await judge.aclose()


def _written(args: argparse.Namespace, done: list[Drafted], failed: list[str]) -> None:
    """Итог файлом — для verify-report: версии промптов, ответы помощника, решения судьи."""
    if args.out is None:
        return
    spoiled_by = f"+spoiled-{args.spoil}" if args.spoil else ""
    payload = {
        "helper_prompt_version": sh.PROMPT_VERSION + spoiled_by,
        "helper_reasoning_effort": args.effort or sh.REASONING_EFFORT,
        "judge_prompt_version": sj.PROMPT_VERSION,
        "metrics_as_is": ev.metrics(_judged(done, helped=False)),
        "metrics_helped": ev.metrics(_judged(done, helped=True)),
        "failed": failed,
        "drafts": [asdict(d) for d in done],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Итог записан: {args.out}")


def _spoiled_words(rule: str) -> str:
    """Что сделано с правилом: снято или подменено обратным."""
    words = RULE_WORDS[rule]
    return f"правило «{words}» наоборот" if rule in INVERTED else f"без правила «{words}»"


def _spoil_caught(rule: str, failed: list[str]) -> str:
    if failed:
        return f"ПОРЧА ПОЙМАНА: {_spoiled_words(rule)} — ворота закрылись"
    return (
        f"ПОРЧА НЕ ПОЙМАНА: {_spoiled_words(rule)} — ворота открыты: черновики правило не "
        "проверяют, дополните их случаями, которые правило держит"
    )


@contextmanager
def effort(chosen: str | None) -> Iterator[str]:
    """Усилие рассуждения помощника на время eval: выбранное — или продуктовое; после eval (и
    при сбое) продуктовое возвращается: замер не меняет помощника экрана. Судья — продуктовый."""
    product = sh.REASONING_EFFORT
    sh.REASONING_EFFORT = chosen or product
    try:
        yield sh.REASONING_EFFORT
    finally:
        sh.REASONING_EFFORT = product


def main(argv: list[str], *, golden_dir: str | None = None) -> int:
    args = _args(argv)
    try:
        rows, cases = _sets(args, golden_dir)
        system = None if args.spoil is None else spoiled(sh.load_prompt(), args.spoil)
    except ev.SetError as exc:
        print(f"НАБОР НЕ ПРОЧИТАН: {exc}")
        return 1
    if system is not None:
        print(f"ПОРЧА ПРОМПТА: {_spoiled_words(args.spoil)} — ворота обязаны закрыться")
    product = sh.REASONING_EFFORT
    with effort(args.effort) as chosen:
        print(f"Усилие помощника: {chosen} (у экрана — {product})")
        done = asyncio.run(_evaluate(rows, cases, args, system))
    failed = report(done, ev.Gates())
    _written(args, done, failed)
    if args.spoil is not None:
        print(_spoil_caught(args.spoil, failed))
    if failed:
        print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
