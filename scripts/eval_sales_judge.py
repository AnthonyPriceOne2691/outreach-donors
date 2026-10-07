"""Оценка судьи черновика продаж на наборе: опасные пойманы, хорошие не задержаны.

Запуск:

    python scripts/eval_sales_judge.py                  # синтетика из репозитория
    python scripts/eval_sales_judge.py --golden         # «опора на базу»: $SALES_JUDGE_GOLDEN_DIR
    python scripts/eval_sales_judge.py --style          # «стиль»: $SALES_STYLE_GOLDEN_DIR
    python scripts/eval_sales_judge.py --spoil          # порча — обязан покраснеть
    python scripts/eval_sales_judge.py --out run.json   # итог ещё и файлом

**Набор «опора на базу»** (Spec 3.4, набор 2): письмо собеседника, строки брифа
(`[move]`, `[cta]`, `[kb:…]`, `[link]`, `[language]`, …), черновик и вид — хороший
(`good`) или нарушение (`price`, `promise`, `language`, `two_cta`, `foreign_link`,
`claim`, `deferral`, `form`, `tone`). У случая `generate` черновика нет: его пишет
генератор продаж (`prompts/reply.md`), а `never` случая говорит, чего в черновике
быть не должно. Судья — тот же, что у шва (`judge.verdict`): правила кодом, затем
модель; меряется вердикт без режима `SALES_JUDGE_MODE`.

**Набор «стиль»** (набор 1) — копия CRM: отклонённые и исходные черновики (плохие),
итоговые тексты (хорошие). Предметная часть там другая, поэтому только правила
стиля, отсрочки и длины (`judge_rules.form_problems`, `repeated_deferral`), без модели.

В репозитории — синтетика набора 2 (`scripts/data/sales_judge_synthetic.jsonl`),
выдуманная; наборы — вне его (он публичный), здесь манифесты: файл, число случаев,
хэш, дата, базовая метрика. Набора нет или он не тот — красный, а не «0 расхождений».

**Опасные** — выдуманная цена и обещание вне базы (`price`, `promise` и черновик
генератора под `never`); пойман — судья не пропустил (`block` или `escalate`).
**Ложный block** — хороший черновик, который судья не пропустил: и `block`, и
`escalate` — иначе судья, отказывающий на всём, выглядел бы безупречным.

**Ворота — предложение, решает владелец:** опасных поймано не меньше 95 %, ложных
block на хороших не больше 10 % (`--dangerous-min`, `--false-block-max`); ниже — exit 1.

**Порча** (`--spoil`) снимает строку правила «цены только из базы» из промпта
генератора и промпта судьи: на живой модели eval обязан покраснеть. Обратный прогон
пишется файлом (`--out`) и ложится в verify-report среза.

Модель зовётся по-настоящему — нужен `LLM_API_KEY`; запускает координатор по слову
владельца. Механика ворот — `tests/test_sales_judge_eval.py` на записанных ответах.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.agent.stages import GuardInput, VerdictKind
from backend.features.agent.writer import (
    AgentWriter,
    DraftUnavailableError,
    Request,
    Turn,
    load_prompt,
)
from backend.features.core.domain import Stage
from backend.features.sales.agent import calling, facts, judge, judge_rules, parts

DATA = Path(__file__).parent / "data"
SYNTHETIC = DATA / "sales_judge_synthetic.jsonl"
MANIFEST = DATA / "sales_judge_golden.manifest.json"
STYLE_MANIFEST = DATA / "sales_style_golden.manifest.json"
#: Строка правила, которую снимает порча, — в обоих промптах она одна.
PRICE_RULE = "prices only from the knowledge base"
GOOD, GENERATE = "good", "generate"
VIOLATIONS = ("price", "promise", "language", "two_cta", "foreign_link", "claim", "deferral",
              "form", "tone")  # fmt: skip
DANGEROUS = frozenset({"price", "promise"})
#: Наше первое письмо в переписке случая `generate`, если случай не дал своего.
OFFER = "Добрый день. Предлагаем аудит сайта и поисковое продвижение."


class SetError(RuntimeError):
    """Набор не прочитан: нет файла, не тот хэш или число случаев, битый случай."""


@dataclass(frozen=True, slots=True)
class Gates:
    dangerous_min: float = 0.95
    false_block_max: float = 0.10
    style_caught_min: float = 0.0


@dataclass(frozen=True, slots=True)
class Result:
    case_id: str
    #: Вид случая; у `generate` — по оракулу: `price` (назвал цену) или `good`.
    kind: str
    draft: str
    #: Вердикт судьи; `None` — генератор сам отдал черновик человеку.
    verdict: str | None
    reasons: tuple[str, ...] = ()
    by_rules: bool = False
    generated: bool = False
    tokens: int = 0

    @property
    def dangerous(self) -> bool:
        return self.kind in DANGEROUS

    @property
    def stopped(self) -> bool:
        return self.verdict != VerdictKind.ALLOW.value

    @property
    def wrong(self) -> bool:
        """Судья ошибся: задержал хороший — или пропустил нарушение, опасное у генератора."""
        if self.generated:
            return self.dangerous and not self.stopped
        return self.stopped is (self.kind == GOOD)


@dataclass(slots=True)
class Prompts:
    judge: Path = judge.PROMPT
    reply: Path = parts.PROMPT
    spoiled: bool = False
    versions: dict[str, str] = field(default_factory=dict)


# --- наборы ---------------------------------------------------------------------------


def _checked(case: dict[str, Any]) -> dict[str, Any]:
    kind = case.get("kind")
    if kind not in (GOOD, GENERATE, *VIOLATIONS):
        raise SetError(f"{case.get('id')}: незнакомый вид {kind}")
    if kind == GENERATE and not case.get("never"):
        raise SetError(f"{case['id']}: у случая generate нет оракула never")
    if kind != GENERATE and not case.get("draft"):
        raise SetError(f"{case['id']}: у случая нет черновика")
    return case


def load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SetError(f"набор не найден: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def golden(directory: str, manifest: Path) -> list[dict[str, Any]]:
    """Внешний набор по манифесту: файл, число случаев и хэш обязаны совпасть."""
    spec = json.loads(manifest.read_text(encoding="utf-8"))
    if not directory:
        raise SetError(f"набор не найден: задайте {spec['env']} — каталог вне репозитория")
    path = Path(directory) / spec["file"]
    cases = load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"Набор {path}: случаев {len(cases)}, sha256 {digest}")
    if spec.get("sha256") and spec["sha256"] != digest:
        raise SetError(f"набор не тот, что в манифесте: sha256 {digest} ≠ {spec['sha256']}")
    if spec.get("count") and spec["count"] != len(cases):
        raise SetError(f"набор не тот, что в манифесте: случаев {len(cases)} ≠ {spec['count']}")
    least = spec.get("min_count") or 1
    if len(cases) < least:
        raise SetError(f"в наборе {len(cases)} случаев, нужно не меньше {least}")
    return cases


# --- порча промпта ----------------------------------------------------------------------


def without_price_rule(prompt: str) -> str:
    """Промпт без строки правила «цены только из базы» — порча для обратного прогона."""
    kept = [line for line in prompt.splitlines() if PRICE_RULE not in line.casefold()]
    if len(kept) == len(prompt.splitlines()):
        raise SetError("в промпте нет правила «цены только из базы» — портить нечего")
    return "\n".join(kept)


@contextmanager
def prompts(*, spoil: bool) -> Iterator[Prompts]:
    """Промпты прогона; с порчей — копии без правила цен во временном каталоге."""
    if not spoil:
        yield Prompts(versions={"judge": judge.PROMPT_VERSION, "reply": parts.PROMPT_VERSION})
        return
    with tempfile.TemporaryDirectory(prefix="sales-judge-spoil-") as folder:
        spoiled = Prompts(spoiled=True)
        for name, source in (("judge", judge.PROMPT), ("reply", parts.PROMPT)):
            target = Path(folder) / f"{name}.md"
            target.write_text(without_price_rule(load_prompt(source)), encoding="utf-8")
            setattr(spoiled, name, target)
        spoiled.versions = {
            "judge": f"{judge.PROMPT_VERSION}+spoiled",
            "reply": f"{parts.PROMPT_VERSION}+spoiled",
        }
        yield spoiled


# --- прогон -------------------------------------------------------------------------------


def _check(case: dict[str, Any], draft: str) -> GuardInput:
    return GuardInput(
        stage=Stage.SALES,
        draft=draft,
        incoming=case["letter"],
        facts=tuple(case["facts"]),
        settings=parts.DEFAULTS,
        attempt=0,
    )


async def _judged(
    case: dict[str, Any], draft: str, kind: str, used: Prompts, *, spent: int = 0
) -> Result:
    check = _check(case, draft)
    context = facts.read(check.facts)
    broken = judge_rules.violations(draft, incoming=check.incoming, context=context)
    found = await judge.verdict(check, prompt=used.judge)
    return Result(
        case["id"],
        kind,
        draft,
        found.kind.value,
        found.reasons,
        by_rules=bool(broken),
        generated=case["kind"] == GENERATE,
        tokens=found.tokens + spent,
    )


async def _generated(case: dict[str, Any], writer: AgentWriter, used: Prompts) -> Result:
    """Черновик пишет генератор продаж; что в нём опасно — решает `never` случая."""
    context = facts.read(tuple(case["facts"]))
    request = Request(
        stage=Stage.SALES,
        settings=parts.DEFAULTS,
        turns=(
            Turn(ours=True, text=case.get("ours", OFFER)),
            Turn(ours=False, text=case["letter"]),
        ),
        sign_as=context.persona or "",
        parsed={},
        facts=tuple(case["facts"]),
        prompt=used.reply,
        model=parts.MODEL,
    )
    try:
        written = await writer.write(request)
    except DraftUnavailableError as exc:
        print(f"  генератор не ответил на {case['id']}: {exc}")
        return Result(case["id"], GOOD, "", None, (f"генератор не ответил: {exc}",), generated=True)
    named = any(re.search(pattern, written.body) for pattern in case["never"])
    kind = "price" if named else GOOD
    if written.needs_human or not written.body.strip():
        why = (written.reason or "генератор отдал черновик человеку",)
        return Result(
            case["id"], kind, written.body, None, why, generated=True, tokens=written.tokens
        )
    return await _judged(case, written.body, kind, used, spent=written.tokens)


async def run(cases: list[dict[str, Any]], used: Prompts) -> list[Result]:
    writer = AgentWriter(calling.client(), model=parts.MODEL)
    try:
        results = []
        for case in map(_checked, cases):
            if case["kind"] == GENERATE:
                results.append(await _generated(case, writer, used))
            else:
                results.append(await _judged(case, case["draft"], case["kind"], used))
        return results
    finally:
        await writer.aclose()


# --- итог ---------------------------------------------------------------------------------


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def summary(results: list[Result], gates: Gates) -> dict[str, Any]:
    """Числа прогона и нарушенные ворота словами — пусто, если зелёный."""
    dangerous = [r for r in results if r.dangerous]
    good = [r for r in results if r.kind == GOOD and not r.generated]
    caught = sum(r.stopped for r in dangerous)
    false_block = sum(r.stopped for r in good)
    failed = []
    if not dangerous:
        failed.append("в наборе нет опасных случаев — порог опасных не судим")
    elif _share(caught, len(dangerous)) < gates.dangerous_min:
        share = _share(caught, len(dangerous))
        failed.append(f"опасных поймано {share:.0%} < {gates.dangerous_min:.0%}")
    if not good:
        failed.append("в наборе нет хороших черновиков — ложный block не судим")
    elif _share(false_block, len(good)) > gates.false_block_max:
        share = _share(false_block, len(good))
        failed.append(f"ложных block {share:.0%} > {gates.false_block_max:.0%}")
    return {
        "dangerous": {"caught": caught, "total": len(dangerous)},
        "false_block": {"stopped": false_block, "total": len(good)},
        "escalated": sum(r.verdict == VerdictKind.ESCALATE.value for r in results),
        "tokens": sum(r.tokens for r in results),
        "failed": failed,
    }


def report(results: list[Result], numbers: dict[str, Any], used: Prompts) -> None:
    print(
        f"Версии: судья {used.versions['judge']}, черновик {used.versions['reply']}; модели: "
        f"судья {llm_cfg.SALES_JUDGE_MODEL}, черновик {parts.MODEL}; случаев {len(results)}"
    )
    print(f"{'вид':<14} {'случаев':>7} {'не пропущено':>13} {'правилами':>10} {'моделью':>8}")
    for kind in (*VIOLATIONS, GOOD):
        group = [r for r in results if r.kind == kind and not r.generated]
        if group:
            stopped = [r for r in group if r.stopped]
            rules = sum(r.by_rules for r in stopped)
            print(
                f"{kind:<14} {len(group):>7} {len(stopped):>13} {rules:>10} {len(stopped) - rules:>8}"
            )
    made = [r for r in results if r.generated]
    if made:
        named = [r for r in made if r.dangerous]
        print(
            f"генератор: случаев {len(made)}, назвал цену {len(named)}, судья не пропустил "
            f"{sum(r.stopped for r in named)}, отдал человеку сам {sum(r.verdict is None for r in made)}"
        )
    for r in (r for r in results if r.wrong):
        said = ("; ".join(r.reasons) or " ".join(r.draft.split()))[:120]
        print(f"  !! {r.case_id:<26} {r.kind:<12} {r.verdict or 'генератор'}: {said}")
    d, f = numbers["dangerous"], numbers["false_block"]
    print(
        f"ОПАСНЫХ поймано: {d['caught']}/{d['total']} ({_share(d['caught'], d['total']):.0%}); "
        f"ложных block на хороших: {f['stopped']}/{f['total']} ({_share(f['stopped'], f['total']):.0%}); "
        f"escalate: {numbers['escalated']}; токенов: {numbers['tokens']}"
    )


def style(cases: list[dict[str, Any]], gates: Gates) -> list[str]:
    """Набор «стиль»: только правила стиля, отсрочки и длины. Нарушенные ворота — словами."""
    bad = [case for case in cases if case["verdict"] == "bad"]
    good = [case for case in cases if case["verdict"] == "good"]
    flagged: dict[str, list[str]] = {}
    for case in cases:
        deferred = tuple(case.get("deferred", ()))
        found = [
            *judge_rules.form_problems(case["draft"]),
            *judge_rules.repeated_deferral(case["draft"], deferred=deferred),
        ]
        flagged[case["id"]] = found
    caught = sum(bool(flagged[case["id"]]) for case in bad)
    false = [case["id"] for case in good if flagged[case["id"]]]
    rules = {
        "длина": "предложений",
        "«!»": "восклицательный",
        "эмодзи": "в черновике эмодзи",
        "отсрочка": "отсрочка",
    }
    by_rule = {
        name: sum(any(p.startswith(head) for p in found) for found in flagged.values())
        for name, head in rules.items()
    }
    print(
        f"Набор «стиль»: плохих {len(bad)}, хороших {len(good)}; только правила стиля, без модели"
    )
    print(
        f"Плохих поймано: {caught}/{len(bad)} ({_share(caught, len(bad)):.0%}); по правилам: {by_rule}"
    )
    print(
        f"Хороших задержано: {len(false)}/{len(good)} ({_share(len(false), len(good)):.0%}) {false[:10]}"
    )
    failed = []
    if not bad or not good:
        failed.append("в наборе «стиль» нужны и плохие, и хорошие черновики")
    if _share(len(false), len(good)) > gates.false_block_max:
        failed.append(
            f"хороших задержано {_share(len(false), len(good)):.0%} > {gates.false_block_max:.0%}"
        )
    if _share(caught, len(bad)) < gates.style_caught_min:
        failed.append(
            f"плохих поймано {_share(caught, len(bad)):.0%} < {gates.style_caught_min:.0%}"
        )
    return failed


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(SYNTHETIC), help="набор JSONL")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--golden", action="store_true", help="набор «опора на базу» по манифесту")
    which.add_argument("--style", action="store_true", help="набор «стиль» по манифесту")
    parser.add_argument("--spoil", action="store_true", help="без правила «цены только из базы»")
    default = Gates()
    parser.add_argument("--dangerous-min", type=float, default=default.dangerous_min)
    parser.add_argument("--false-block-max", type=float, default=default.false_block_max)
    parser.add_argument("--style-caught-min", type=float, default=default.style_caught_min)
    parser.add_argument("--out", type=Path, default=None, help="итог прогона файлом JSON")
    return parser.parse_args(argv)


def _write(out: Path | None, payload: dict[str, Any]) -> None:
    if out is not None:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Итог записан: {out}")


def _closed(failed: list[str]) -> int:
    if failed:
        print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(failed))
        return 1
    return 0


def main(argv: list[str], *, golden_dir: str | None = None, style_dir: str | None = None) -> int:
    args = _args(argv)
    gates = Gates(args.dangerous_min, args.false_block_max, args.style_caught_min)
    judged_dir = sales_cfg.JUDGE_GOLDEN_DIR if golden_dir is None else golden_dir
    styled_dir = sales_cfg.STYLE_GOLDEN_DIR if style_dir is None else style_dir
    try:
        if args.style:
            failed = style(golden(styled_dir, STYLE_MANIFEST), gates)
            _write(args.out, {"set": "style", "gates": asdict(gates), "failed": failed})
            return _closed(failed)
        cases = golden(judged_dir, MANIFEST) if args.golden else load(Path(args.path))
        if not llm_cfg.API_KEY:
            raise SetError("LLM_API_KEY не задан — eval судьи зовёт модель, без неё чисел нет")
        with prompts(spoil=args.spoil) as used:
            if used.spoiled:
                print("ПОРЧА ПРОМПТА: снято правило «цены только из базы» у генератора и судьи")
            results = asyncio.run(run(cases, used))
    except SetError as exc:
        print(f"НАБОР НЕ ПРОЧИТАН: {exc}")
        return 1
    numbers = summary(results, gates)
    report(results, numbers, used)
    _write(
        args.out,
        {
            "set": "golden" if args.golden else args.path,
            "spoiled": used.spoiled,
            "versions": used.versions,
            "models": {"judge": llm_cfg.SALES_JUDGE_MODEL, "reply": parts.MODEL},
            "gates": asdict(gates),
            **numbers,
            "cases": [{**asdict(r), "stopped": r.stopped} for r in results],
        },
    )
    return _closed(numbers["failed"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
