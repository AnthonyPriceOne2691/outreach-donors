"""Оценка судьи сегмента сбора базы на наборе: ложные «принять», ложные «отказ», полнота.

Запуск:

    python scripts/eval_sales_segment.py                      # синтетика из репозитория
    python scripts/eval_sales_segment.py --golden             # внешний набор: $SALES_SEGMENT_GOLDEN_DIR
    python scripts/eval_sales_segment.py --spoil              # порча: снято правило дословной цитаты
    python scripts/eval_sales_segment.py --spoil no           # порча: снято определение «no»
    python scripts/eval_sales_segment.py --spoil about        # порча: снято правило v2 — это промпт v1
    python scripts/eval_sales_segment.py --effort minimal     # другое усилие рассуждения — замер
    python scripts/eval_sales_segment.py --out run.json       # итог ещё и файлом
    python scripts/eval_sales_segment.py --export-run 12 --out set.jsonl   # сайты прогона — в разметку

**Случай** — сайт, как его видел судья: гипотеза, описание сегмента, по которому размечено,
домен, текст сайта строками с подписями (`Заголовок в выдаче: …`, `Меню главной: …` — тот же
вид, что хранится у кандидата сбора), решение человека `expect` — `yes`, `no` или `unsure`,
а когда человек сам не уверен — `yes|unsure` или `no|unsure`: верен любой из двух ответов,
и `evidence` — слова сайта, на которых решение стоит. Судья — тот же, что у прогона
(`segment_judge.decide`): правила до модели, модель, дословная цитата; меряется его полоса
без режима `SALES_SEGMENT_JUDGE_MODE` — режим решает, резать ли, а мерить надо, прав ли судья.

В репозитории — синтетика (`scripts/data/sales_segment_synthetic.jsonl`), выдуманная: ниши и
языки разные, ловушки «сайт о бизнесах вида» (справочник, карта, реестр, площадка, тендеры,
вакансии, агрегатор, новости, обзор — метка `about-businesses`) и под строгим описанием, и под
нестрогим («сайты, связанные с …», метка `loose`), контрольные сегменты, которые сами просят
справочники и площадки (метка `control`). Живой набор — от 40 размеченных сайтов на гипотезу —
вне репозитория (он публичный): манифест `scripts/data/sales_segment_golden.manifest.json`
называет файл, число случаев, хэш, дату и базовые метрики. Набора нет, он не тот, мал или не
закреплён в манифесте (нет числа и хэша) — красный, а не «0 расхождений». Сайты для разметки
даёт `--export-run N`: кандидаты прогона с текстом, который видел судья, без вердикта судьи
(размечающий его не видит) и с пустым `expect`.

**Метрики при `yes|unsure` и `no|unsure`:** случай с `yes` среди ответов — в знаменателе
полноты «принять» и ложных «отказ», с `no` — полноты «отказ»; без `yes` — ложных «принять».
Верный ответ (любой из двух) — попадание в полноту; «принять» без `yes` среди ответов — ложное
«принять», «отказ» при `yes` среди ответов — ложный «отказ». Так `no|unsure`, принятый судьёй, —
ложное «принять», а «на проверку» — не промах.

**Ворота — предложение, решает владелец:** ложных «принять» среди неподходящих — не больше 5 %
(такой сайт стал бы лидом без человека), ложных «отказ» среди подходящих — не больше 10 %,
полнота «принять» и «отказ» — не ниже 70 % (иначе всё уходит человеку и судья не нужен).

**Порча** (`--spoil`) снимает строку правила из промпта (`RULES`): на живой модели eval обязан
покраснеть — итог говорит словами, поймана ли порча. По умолчанию — дословная цитата: без неё
цитаты пересказаны и всё уходит человеку. `about` — правило v2 «сайт о бизнесах вида — не
бизнес вида»: без него промпт — ровно v1, и ложные «принять» на ловушках `about-businesses`
печатаются отдельной строкой. Обратный прогон пишется файлом (`--out`) и ложится в
verify-report среза.

**Усилие рассуждения** (`--effort`) — вместо продуктового (`segment_judge.REASONING_EFFORT`) на
время eval: замер до правки продукта — тот же набор при `low` и `minimal`, ворота и токены рядом.
Токены — всего и раздельно (ввод, вывод, сколько из вывода — рассуждение, «раздельно
неизвестно»): рассуждение идёт в счёт вывода, а у вывода своя цена.

Модель зовётся по-настоящему — нужен `LLM_API_KEY`; запускает координатор по слову владельца.
Механика ворот — `tests/test_sales_segment_eval.py` на подставной модели.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from operator import attrgetter
from pathlib import Path
from typing import Any

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_judge as sj
from backend.features.sales.models import SalesHypothesisModel as H
from backend.features.sales.segment_judge import SegmentClient, SegmentVerdict, SiteText
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_models import SalesSegmentCandidateModel as C
from backend.features.sales.segment_window import SPLIT_TOKENS, JudgeTally
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATA = Path(__file__).parent / "data"
SYNTHETIC = DATA / "sales_segment_synthetic.jsonl"
MANIFEST = DATA / "sales_segment_golden.manifest.json"
#: Решение человека → полоса, которую ждём от судьи.
WANTED = {"yes": Lane.ACCEPT, "no": Lane.REJECT, "unsure": Lane.REVIEW}
#: Что можно написать в `expect`: решение или пара «решение|не понять» — верен любой ответ пары.
EXPECTS = (*WANTED, "yes|unsure", "no|unsure")
#: Правила, которые снимает порча, — якорь строки в промпте: строка с ним одна.
RULES = {
    "quote": '"quote" must be copied verbatim',
    "no": '- "no": ',
    "about": "- Judge what the site is, not what it writes about.",
}
RULE_WORDS = {
    "quote": "дословная цитата",
    "no": "определение «не подходит»",
    "about": "сайт о бизнесах вида — не бизнес вида",
}
#: Случаи, которые держит правило, — метка набора: их ложные «принять» печатаются отдельно.
RULE_TAGS = {"about": "about-businesses"}
#: Усилия рассуждения, которые eval берёт вместо продуктового (`--effort`).
EFFORTS = ("minimal", "low", "medium", "high")


class SetError(RuntimeError):
    """Набор не прочитан: нет файла, не тот хэш или число случаев, битый случай."""


@dataclass(frozen=True, slots=True)
class Gates:
    false_accept_max: float = 0.05
    false_reject_max: float = 0.10
    accept_recall_min: float = 0.70
    reject_recall_min: float = 0.70


@dataclass(frozen=True, slots=True)
class Result:
    case_id: str
    hypothesis: str
    expect: str
    lane: str
    reason: str
    quote: str | None
    decided_by: str
    unanswered: bool
    tokens: int
    #: Те же токены раздельно (`segment_window.SPLIT_TOKENS`): ввод, вывод, «раздельно
    #: неизвестно», сколько из вывода — рассуждение.
    split: dict[str, int] = dataclasses.field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @property
    def answers(self) -> frozenset[str]:
        """Верные ответы судьи: решение человека или любой из пары `yes|unsure`, `no|unsure`."""
        return frozenset(self.expect.split("|"))

    @property
    def right(self) -> bool:
        return self.lane in {WANTED[answer] for answer in self.answers}

    @property
    def false_accept(self) -> bool:
        """Неподходящий сайт принят без человека — он стал бы лидом."""
        return "yes" not in self.answers and self.lane == Lane.ACCEPT

    @property
    def false_reject(self) -> bool:
        """Подходящий сайт отсечён — в `enforce` потерянный лид."""
        return "yes" in self.answers and self.lane == Lane.REJECT


# --- наборы ---------------------------------------------------------------------------------


def _checked(case: dict[str, Any]) -> dict[str, Any]:
    for key in ("id", "hypothesis", "segment", "host", "text"):
        if not isinstance(case.get(key), str):
            raise SetError(f"{case.get('id')}: нет поля {key}")
    if case.get("expect") not in EXPECTS:
        raise SetError(f"{case['id']}: не размечен — expect: {' | '.join(EXPECTS)}")
    return case


def load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SetError(f"набор не найден: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_checked(json.loads(line)) for line in lines if line.strip()]


def golden(directory: str, manifest: Path | None = None) -> list[dict[str, Any]]:
    """Внешний набор по манифесту: файл, число случаев, хэш и размер на гипотезу. Набор, не
    закреплённый в манифесте числом и хэшем, не судится: не тот файл прошёл бы молча."""
    spec = json.loads((manifest or MANIFEST).read_text(encoding="utf-8"))
    if not directory:
        raise SetError(f"набор не найден: задайте {spec['env']} — каталог вне репозитория")
    path = Path(directory) / spec["file"]
    cases = load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"Набор {path}: случаев {len(cases)}, sha256 {digest}")
    _pinned(spec, len(cases), digest)
    least = int(spec["per_hypothesis"]["min"])
    sizes = Counter(case["hypothesis"] for case in cases)
    if small := {name: count for name, count in sizes.items() if count < least}:
        raise SetError(f"набор мал: {small} — на гипотезу нужно не меньше {least} сайтов")
    if base := spec.get("baseline"):
        print(f"База манифеста: {json.dumps(base, ensure_ascii=False)}")
    return cases


def _pinned(spec: dict[str, Any], count: int, digest: str) -> None:
    """Набор — тот, что закреплён в манифесте числом случаев и хэшем; не закреплён — отказ."""
    if not spec.get("sha256") or not spec.get("count"):
        raise SetError(
            f"набор не закреплён в манифесте: впишите count {count} и sha256 {digest} "
            "(их напечатала эта проверка), когда набор утверждён"
        )
    if spec["sha256"] != digest:
        raise SetError(f"набор не тот, что в манифесте: sha256 {digest} ≠ {spec['sha256']}")
    if spec["count"] != count:
        raise SetError(f"набор не тот, что в манифесте: случаев {count} ≠ {spec['count']}")


def without_rule(prompt: str, rule: str) -> str:
    """Промпт без строки правила `rule` (`RULES`) — порча для обратного прогона."""
    kept = [line for line in prompt.splitlines() if RULES[rule] not in line]
    if len(kept) == len(prompt.splitlines()):
        raise SetError(f"в промпте нет правила «{RULE_WORDS[rule]}» — портить нечего")
    return "\n".join(kept)


# --- прогон ------------------------------------------------------------------------------------


def split_of(verdict: SegmentVerdict) -> dict[str, int]:
    """Токены вердикта раздельно — тем же счётом, что у прохода судьи (`JudgeTally.spend`)."""
    tally = JudgeTally()
    tally.spend(verdict)
    return {key: getattr(tally, key) for key in SPLIT_TOKENS}


@contextmanager
def effort(chosen: str | None) -> Iterator[str]:
    """Усилие рассуждения на время eval: выбранное — или продуктовое; после eval (и при сбое)
    продуктовое возвращается: замер не меняет судью прогона."""
    product = sj.REASONING_EFFORT
    sj.REASONING_EFFORT = chosen or product
    try:
        yield sj.REASONING_EFFORT
    finally:
        sj.REASONING_EFFORT = product


async def run(
    cases: Iterable[dict[str, Any]], client: SegmentClient, *, system: str | None = None
) -> list[Result]:
    results = []
    for case in cases:
        verdict = await sj.decide(
            client,
            host=case["host"],
            segment=case["segment"],
            text=SiteText.parse(case["text"]),
            system=system,
        )
        results.append(
            Result(
                case["id"],
                case["hypothesis"],
                case["expect"],
                verdict.lane.value,
                verdict.reason,
                verdict.quote,
                verdict.decided_by.value,
                verdict.unanswered,
                verdict.tokens,
                split_of(verdict),
                tuple(case.get("tags") or ()),
            )
        )
    return results


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _groups(results: list[Result]) -> tuple[list[Result], list[Result], list[Result]]:
    """Знаменатели: с `yes` среди ответов, без `yes`, с `no` (пары — в шапке модуля)."""
    fits = [r for r in results if "yes" in r.answers]
    misfits = [r for r in results if "yes" not in r.answers]
    cuts = [r for r in results if "no" in r.answers]
    return fits, misfits, cuts


def _rate(group: list[Result], field: str) -> float | None:
    """Доля случаев группы, у которых признак `field` истинен."""
    return _share(sum(map(attrgetter(field), group)), len(group))


def metrics(results: list[Result]) -> dict[str, Any]:
    """Числа ворот: полнота «принять» и «отказ», доли ложных — от своего знаменателя (как
    считаются `yes|unsure` и `no|unsure` — в шапке модуля)."""
    fits, misfits, cuts = _groups(results)
    lanes = Counter(r.lane for r in results)
    return {
        "cases": len(results),
        "accept_recall": _rate(fits, "right"),
        "reject_recall": _rate(cuts, "right"),
        "false_accept": _rate(misfits, "false_accept"),
        "false_reject": _rate(fits, "false_reject"),
        "to_human": _share(lanes[Lane.REVIEW], len(results)),
        "unanswered": sum(r.unanswered for r in results),
        "tokens": sum(r.tokens for r in results),
        **{key: sum(r.split.get(key, 0) for r in results) for key in SPLIT_TOKENS},
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


#: Ворота: число итога, поле `Gates`, слова, потолок (`True`) или пол.
CHECKS = (
    ("false_accept", "false_accept_max", "ложных «принять»", True),
    ("false_reject", "false_reject_max", "ложных «отказ»", True),
    ("accept_recall", "accept_recall_min", "полнота «принять»", False),
    ("reject_recall", "reject_recall_min", "полнота «отказ»", False),
)


def closed(total: dict[str, Any], gates: Gates) -> list[str]:
    """Нарушенные ворота словами — пусто, если зелёный. Числа без знаменателя не судятся."""
    failed = []
    for key, field, words, ceiling in CHECKS:
        value, gate = total[key], getattr(gates, field)
        if value is not None and (value > gate if ceiling else value < gate):
            failed.append(f"{words} {value:.0%} {'>' if ceiling else '<'} {gate:.0%}")
    return failed


def _by_hypothesis(results: list[Result]) -> None:
    for name in sorted({r.hypothesis for r in results}):
        part = metrics([r for r in results if r.hypothesis == name])
        print(
            f"  {name}: «принять» {_percent(part['accept_recall'])}, «отказ» "
            f"{_percent(part['reject_recall'])}, ложных «принять» {_percent(part['false_accept'])}, "
            f"ложных «отказ» {_percent(part['false_reject'])}, человеку {_percent(part['to_human'])}"
        )


def report(results: list[Result], gates: Gates) -> list[str]:
    """Печать итога — всего и по гипотезам. Возвращает нарушенные ворота словами."""
    print(f"Версия промпта: {sj.PROMPT_VERSION}, случаев {len(results)}")
    _by_hypothesis(results)
    total = metrics(results)
    print(f"Всего: {total}")
    wrong = Counter(f"{r.expect}→{r.lane}" for r in results if not r.right)
    print(f"Ждали → судья: {dict(wrong)}")
    dangerous = [r.case_id for r in results if r.false_accept]
    print(f"ЛОЖНЫХ «ПРИНЯТЬ»: {len(dangerous)} {dangerous or ''}")
    _traps(results)
    return closed(total, gates)


def _traps(results: list[Result]) -> None:
    """Ложные «принять» на случаях, которые держит правило (`RULE_TAGS`), — отдельной строкой:
    так видно, что сделала порча правила, а что — остальной промпт."""
    for rule, tag in RULE_TAGS.items():
        if traps := [r for r in results if tag in r.tags]:
            caught = [r.case_id for r in traps if r.false_accept]
            print(
                f"Ловушки правила «{RULE_WORDS[rule]}» ({tag}): ложных «принять» "
                f"{len(caught)} из {len(traps)} {caught or ''}"
            )


# --- выгрузка сайтов прогона в разметку ------------------------------------------------------


async def cases_of_run(session: AsyncSession, run_id: int) -> list[dict[str, Any]]:
    """Сайты прогона — случаями без решения: размечает человек (`expect`, `evidence`). Вердикта
    судьи в заготовке нет: размечающий его не видит, иначе разметка повторила бы судью."""
    rows = await session.execute(
        select(C.id, H.name, H.description, DomainModel.host, C.site_text)
        .join(DomainModel, DomainModel.id == C.domain_id)
        .join(H, H.id == C.hypothesis_id)
        .where(C.run_id == run_id, C.site_text.is_not(None))
        .order_by(C.id)
    )
    return [
        {"id": f"run{run_id}-{cid}", "hypothesis": name, "segment": segment or "", "host": host,
         "text": text, "expect": None, "evidence": "", "labeled_by": "", "labeled_at": "", "tags": []}
        for cid, name, segment, host, text in rows.tuples()
    ]  # fmt: skip


async def _export(run_id: int) -> list[dict[str, Any]]:
    """Заготовка разметки прогона из базы настроек (`STORAGE_DSN`)."""
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine)() as session:
            return await cases_of_run(session, run_id)
    finally:
        await engine.dispose()


# --- запуск --------------------------------------------------------------------------------------


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(SYNTHETIC), help="набор JSONL")
    parser.add_argument("--golden", action="store_true", help="внешний набор по манифесту")
    parser.add_argument("--spoil", nargs="?", const="quote", choices=sorted(RULES), default=None)
    parser.add_argument("--model", default=None, help="модель вместо пина LLM_SALES_SEGMENT_MODEL")
    parser.add_argument(
        "--effort", choices=EFFORTS, default=None, help="усилие рассуждения вместо продуктового"
    )
    parser.add_argument("--out", type=Path, default=None, help="итог файлом JSON / выгрузка JSONL")
    parser.add_argument("--export-run", type=int, default=None, help="сайты прогона — в разметку")
    return parser.parse_args(argv)


async def _evaluate(
    cases: list[dict[str, Any]], args: argparse.Namespace, system: str | None
) -> list[Result]:
    client = SegmentClient(model=args.model)
    try:
        return await run(cases, client, system=system)
    finally:
        await client.aclose()


def _export_to(run_id: int, out: Path | None) -> int:
    if out is None:
        print("Выгрузке нужен файл: --out set.jsonl")
        return 2
    cases = asyncio.run(_export(run_id))
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    print(f"Сайтов прогона №{run_id} к разметке: {len(cases)} → {out}")
    return 0


def _written(args: argparse.Namespace, results: list[Result], failed: list[str]) -> None:
    """Итог файлом — для verify-report: версия промпта, числа, нарушенные ворота, случаи."""
    if args.out is None:
        return
    spoiled = f"+spoiled-{args.spoil}" if args.spoil else ""
    payload = {
        "prompt_version": sj.PROMPT_VERSION + spoiled,
        "reasoning_effort": args.effort or sj.REASONING_EFFORT,
        "metrics": metrics(results),
        "failed": failed,
        "results": [asdict(r) for r in results],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Итог записан: {args.out}")


def _spoil_caught(rule: str, failed: list[str]) -> str:
    """Итог обратного прогона словами: порча обязана закрыть ворота."""
    if failed:
        return f"ПОРЧА ПОЙМАНА: без правила «{RULE_WORDS[rule]}» ворота закрылись"
    return (
        f"ПОРЧА НЕ ПОЙМАНА: без правила «{RULE_WORDS[rule]}» ворота открыты — набор правило не "
        "проверяет, дополните его случаями, которые правило держит"
    )


def main(argv: list[str], *, golden_dir: str | None = None) -> int:
    args = _args(argv)
    if args.export_run is not None:
        return _export_to(args.export_run, args.out)
    where = sales_cfg.SEGMENT_GOLDEN_DIR if golden_dir is None else golden_dir
    try:
        cases = golden(where) if args.golden else load(Path(args.path))
        system = None if args.spoil is None else without_rule(sj.load_prompt(), args.spoil)
    except SetError as exc:
        print(f"НАБОР НЕ ПРОЧИТАН: {exc}")
        return 1
    if system is not None:
        print(f"ПОРЧА ПРОМПТА: снято правило «{RULE_WORDS[args.spoil]}» — ворота обязаны закрыться")
    product = sj.REASONING_EFFORT
    with effort(args.effort) as chosen:
        print(f"Усилие рассуждения: {chosen} (у судьи прогона — {product})")
        results = asyncio.run(_evaluate(cases, args, system))
    failed = report(results, Gates())
    _written(args, results, failed)
    if args.spoil is not None:
        print(_spoil_caught(args.spoil, failed))
    if failed:
        print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
