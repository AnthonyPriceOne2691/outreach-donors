"""Оценка судьи сегмента сбора базы на наборе: ложные «принять», ложные «отказ», полнота.

Запуск:

    python scripts/eval_sales_segment.py                      # синтетика из репозитория
    python scripts/eval_sales_segment.py --golden             # внешний набор: $SALES_SEGMENT_GOLDEN_DIR
    python scripts/eval_sales_segment.py --spoil              # порча: снято правило дословной цитаты
    python scripts/eval_sales_segment.py --spoil places       # порча: места судит модель, а не код
    python scripts/eval_sales_segment.py --spoil intermediary # порча: снято определение посредника
    python scripts/eval_sales_segment.py --golden --other accept --exclude UA  # поле гипотезы
    python scripts/eval_sales_segment.py --golden --from-facts run.json --other accept  # без модели
    python scripts/eval_sales_segment.py --golden --paired v3.json                     # парно с v3
    python scripts/eval_sales_segment.py --prompt-file v2.md --no-home --no-language   # замер v2
    python scripts/eval_sales_segment.py --golden --fetch-home homes.jsonl            # дочитать главные
    python scripts/eval_sales_segment.py --golden --home homes.jsonl --home-chars 800  # с текстом главной
    python scripts/eval_sales_segment.py --effort minimal     # другое усилие рассуждения — замер
    python scripts/eval_sales_segment.py --out run.json       # итог ещё и файлом
    python scripts/eval_sales_segment.py --export-run 12 --out set.jsonl   # сайты прогона — в разметку

**Судья v4** (`segment_judge`): модель отдаёт факты, полосу ставит таблица правил по фактам, разбору
описания и полю гипотезы «страны компаний» — поле у случая (`foreign_countries`, `not_write`) или
ключами `--other`, `--exclude` (`eval_sales_segment_field.py`). Описание разбирается один раз на
набор; факты и разбор ложатся в итог (`--out`), и `--from-facts` ставит полосы заново по ним без
модели — правка поля или таблицы правил меряется на тех же фактах. У каждой доли ложных — граница
Клоппера–Пирсона, сравнение с прошлым итогом — парно на одних сайтах (`eval_sales_segment_stats.py`).

**Случай** — сайт, как его видел судья: гипотеза, описание сегмента, по которому размечено,
домен, текст сайта строками с подписями (`Заголовок в выдаче: …`, `Текст главной: …` — тот же
вид, что хранится у кандидата сбора), решение человека `expect` — `yes`, `no` или `unsure`,
а когда человек сам не уверен — `yes|unsure` или `no|unsure`: верен любой из двух ответов,
`evidence` — слова сайта, на которых решение стоит, и `letters` — язык писем (у живого набора —
полем манифеста): сайт уверенно на другом языке судья отдаёт в «отказал бы» без модели
(`segment_judge.by_language`). Судья — тот же, что у прогона (`segment_judge.decide`): правила до
модели, язык, модель, дословная цитата; меряется его полоса без режима `SALES_SEGMENT_JUDGE_MODE`.

В репозитории — синтетика (`scripts/data/sales_segment_synthetic.jsonl`), выдуманная: ниши,
языки и письменности разные; ловушки «сайт о бизнесах вида» (метка `about-businesses`) под строгим
и нестрогим описанием (`loose`), контрольные сегменты, которые сами просят справочники (`control`);
под v3 — признак сегмента не виден (`unseen`, `in-addition`) и прямое противоречие
(`contradiction`), сайт, где видна только чужая география (`foreign-only` — «не понять»), и его
пара со страной сегмента среди других (`segment-among` — «да»), посредники по сигналам текста
(`intermediary`) и свои формы и калькуляторы (`intermediary-lookalike`), язык сайта (`language`,
двуязычный — `bilingual`) и вид сайта, который показывает только текст главной (`home-text`). Живой набор — вне репозитория (он публичный):
манифест `scripts/data/sales_segment_golden.manifest.json` называет файл, число случаев, хэш, дату,
язык писем, метки случаев без разметки и базу метрик (наборы — `eval_sales_segment_sets.py`).

**Метрики при `yes|unsure` и `no|unsure`:** случай с `yes` среди ответов — в знаменателе
полноты «принять» и ложных «отказ», с `no` — полноты «отказ»; без `yes` — ложных «принять».
Ложные «отказ» — ещё и по классу: язык сайта (`by_language`), посредник (модель назвала вид
`intermediary`), правило, остальное — сегмент. Доли полос — по всем случаям, с разметкой и без:
**вариант (b)** — «на проверку» только сомнительные (`to_human`), отказы судьи — отдельно
(`would_reject`, своя полоса в наблюдении); до варианта (b) на проверку шли и те и другие
(`to_human_shadow`).

**Ворота — предложение, решает владелец:** ложных «принять» среди неподходящих — не больше 5 %
(такой сайт стал бы лидом без человека), ложных «отказ» среди подходящих — не больше 10 %,
полнота «принять» и «отказ» — не ниже 70 % (иначе всё уходит человеку и судья не нужен).

**Порча** (`--spoil`) снимает строку правила из промпта (`RULES`) или ставит вместо неё прежнюю
(`SPOILED_WITH`): на живой модели eval обязан покраснеть — итог говорит словами, поймана ли порча, а
ошибки на ловушках правила (`RULE_TAGS`) печатает отдельной строкой. Обратный прогон пишется файлом
(`--out`) и ложится в verify-report среза.

**Усилие рассуждения** (`--effort`) — вместо продуктового на время eval. Токены — всего, раздельно
(ввод, вывод, сколько из вывода — рассуждение, «раздельно неизвестно») и на вызов модели.

Модель зовётся по-настоящему — нужен `LLM_API_KEY`. Механика — `tests/test_sales_segment_eval*.py`
на подставной модели.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.config import sales as sales_cfg
from backend.features.sales import segment_client
from backend.features.sales import segment_judge as sj
from backend.features.sales.hypothesis_countries import CountryPolicy
from backend.features.sales.segment_judge import SegmentClient, SegmentVerdict, SiteText
from backend.features.sales.segment_window import SPLIT_TOKENS, JudgeTally
from scripts.eval_sales_segment_field import (
    FactsError,
    policy_of_args,
    policy_of_case,
    redecided,
    result_of,
    saved,
    scope_of,
    scopes_of,
)
from scripts.eval_sales_segment_home import (
    HomesError,
    fetched_to,
    read_homes,
    with_home,
    without_home,
)
from scripts.eval_sales_segment_metrics import Result, metrics
from scripts.eval_sales_segment_report import CHECKS, Gates, closed, print_total
from scripts.eval_sales_segment_sets import WANTED, SetError, export_to, load
from scripts.eval_sales_segment_sets import golden as golden_set
from scripts.eval_sales_segment_stats import paired

#: Что eval отдаёт наружу (eval помощника описания и тесты): наборы и метрики живут соседними
#: модулями, имена — и отсюда, как до разреза.
__all__ = [
    "CHECKS",
    "DATA",
    "EFFORTS",
    "MANIFEST",
    "RULES",
    "RULE_TAGS",
    "RULE_WORDS",
    "SPOILED_WITH",
    "SYNTHETIC",
    "WANTED",
    "Gates",
    "Result",
    "SetError",
    "closed",
    "effort",
    "golden",
    "judged",
    "load",
    "main",
    "metrics",
    "report",
    "run",
    "split_of",
    "without_rule",
]

DATA = Path(__file__).parent / "data"
SYNTHETIC = DATA / "sales_segment_synthetic.jsonl"
MANIFEST = DATA / "sales_segment_golden.manifest.json"
#: Правила, которые снимает порча, — якорь строки в промпте: строка с ним одна.
RULES = {
    "quote": '- "quote", "home_quote" and the quotes in "served" must be copied verbatim',
    "no": '- "no": the site is clearly of another kind',
    "intermediary": '- "intermediary": a site that brings customers together',
    "places": "Places are compared by our code, never here",
}
#: Порча, которая ставит вместо правила прежнюю строку: места судит модель — как до v3.
SPOILED_WITH = {
    "places": "Places matter here: a business based or working outside the segment's places is no.",
}
RULE_WORDS = {
    "quote": "дословная цитата",
    "no": "определение «другое дело»",
    "intermediary": "посредник по сигналам в тексте",
    "places": "места сверяет код, а не модель",
}
#: Случаи, которые держит правило, — метка набора: их ошибки печатаются отдельно.
RULE_TAGS = {
    "intermediary": "intermediary",
    "places": "unseen",
}
#: Усилия рассуждения, которые eval берёт вместо продуктового (`--effort`).
EFFORTS = ("minimal", "low", "medium", "high")
#: Сайтов к модели разом: замер, а не прогон — скромнее окна судьи (`SALES_SEGMENT_JUDGE_CONCURRENCY`).
AT_ONCE = 8


def golden(directory: str, manifest: Path | None = None) -> list[dict[str, Any]]:
    """Внешний набор по манифесту — случаи, как до разреза (eval помощника описания); язык писем и
    метки набора — `eval_sales_segment_sets.golden`."""
    return golden_set(directory, manifest or MANIFEST)[0]


def without_rule(prompt: str, rule: str) -> str:
    """Промпт без строки правила `rule` (`RULES`) — или с прежней строкой вместо неё
    (`SPOILED_WITH`): порча для обратного прогона."""
    instead = SPOILED_WITH.get(rule)
    lines = prompt.splitlines()
    kept = [instead if RULES[rule] in line else line for line in lines]
    spoiled = [line for line in kept if line is not None]
    if all(RULES[rule] not in line for line in lines):
        raise SetError(f"в промпте нет правила «{RULE_WORDS[rule]}» — портить нечего")
    return "\n".join(spoiled)


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
    product = segment_client.REASONING_EFFORT
    segment_client.REASONING_EFFORT = chosen or product
    try:
        yield segment_client.REASONING_EFFORT
    finally:
        segment_client.REASONING_EFFORT = product


async def judged(
    cases: list[dict[str, Any]],
    client: SegmentClient,
    *,
    system: str | None = None,
    letters: str | None = None,
    language: bool = True,
    policy: CountryPolicy | None = None,
) -> tuple[list[Result], dict[str, dict[str, Any]]]:
    """Судья по случаям — до `AT_ONCE` разом, итог в порядке набора, и разбор описаний (один
    вызов на описание). Язык писем — случая, иначе набора (`letters`); `language=False` — без
    сверки языка, как до v3. Поле гипотезы — случая, иначе `policy` (`policy_of_case`)."""
    gate = asyncio.Semaphore(AT_ONCE)
    scopes = await scopes_of(cases, client)
    fallback = policy or CountryPolicy()

    async def one(case: dict[str, Any]) -> Result:
        async with gate:
            verdict = await sj.decide(
                client,
                host=case["host"],
                segment=case["segment"],
                text=SiteText.parse(case["text"]),
                scope=scope_of(scopes, case["segment"]),
                policy=policy_of_case(case, fallback),
                system=system,
                letters=(case.get("letters") or letters) if language else None,
            )
        return result_of(case, verdict, split_of(verdict))

    return list(await asyncio.gather(*(one(case) for case in cases))), scopes


async def run(
    cases: Iterable[dict[str, Any]],
    client: SegmentClient,
    *,
    system: str | None = None,
    letters: str | None = None,
    language: bool = True,
) -> list[Result]:
    """Судья по случаям без разбора описаний в итоге — eval помощника описания."""
    found, _ = await judged(list(cases), client, system=system, letters=letters, language=language)
    return found


def report(
    results: list[Result], gates: Gates, scopes: dict[str, dict[str, Any]] | None = None
) -> list[str]:
    """Печать итога — всего и по гипотезам (`eval_sales_segment_report`). Возвращает нарушенные
    ворота словами."""
    print(f"Версия промпта: {sj.PROMPT_VERSION}, случаев {len(results)}")
    total = print_total(results, scopes)
    _traps([r for r in results if r.labeled])
    return closed(total, gates)


def _traps(results: list[Result]) -> None:
    """Ошибки на случаях, которые держит правило (`RULE_TAGS`), — отдельной строкой: так видно,
    что сделала порча правила, а что — остальной промпт."""
    for rule, tag in RULE_TAGS.items():
        if traps := [r for r in results if tag in r.tags]:
            caught = [r.case_id for r in traps if r.false_accept]
            cut = [r.case_id for r in traps if r.false_reject]
            print(
                f"Ловушки правила «{RULE_WORDS[rule]}» ({tag}): ложных «принять» "
                f"{len(caught)} из {len(traps)} {caught or ''}; ложных «отказ» {len(cut)} {cut or ''}"
            )


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
    parser.add_argument("--prompt-file", type=Path, default=None, help="промпт вместо продуктового")
    parser.add_argument("--no-home", action="store_true", help="без строки «Текст главной»")
    parser.add_argument("--no-language", action="store_true", help="без сверки языка сайта")
    parser.add_argument("--letters", default=None, help="язык писем вместо языка набора")
    parser.add_argument("--home", type=Path, default=None, help="дочитанные главные к набору")
    parser.add_argument("--home-chars", type=int, default=None, help="потолок текста главной")
    parser.add_argument("--fetch-home", type=Path, default=None, help="дочитать главные в файл")
    parser.add_argument("--manifest", type=Path, default=None, help="манифест другого набора")
    parser.add_argument(
        "--other", choices=("review", "accept"), default="review", help="компании из других стран"
    )
    parser.add_argument("--exclude", default="", help="не пишем компаниям из: коды через запятую")
    parser.add_argument("--from-facts", type=Path, default=None, help="полосы по фактам итога")
    parser.add_argument("--paired", type=Path, default=None, help="итог для парного сравнения")
    return parser.parse_args(argv)


async def _evaluate(
    cases: list[dict[str, Any]], args: argparse.Namespace, system: str | None, letters: str | None
) -> tuple[list[Result], dict[str, dict[str, Any]]]:
    client = SegmentClient(model=args.model)
    try:
        return await judged(
            cases,
            client,
            system=system,
            letters=letters,
            language=not args.no_language,
            policy=policy_of_args(args.other, args.exclude),
        )
    finally:
        await client.aclose()


def _written(
    args: argparse.Namespace,
    results: list[Result],
    failed: list[str],
    scopes: dict[str, dict[str, Any]],
) -> None:
    """Итог файлом — для verify-report: версия промпта, поле гипотезы запуска, числа, нарушенные
    ворота, разбор описаний и случаи с фактами (по ним `--from-facts` ставит полосы без модели)."""
    if args.out is None:
        return
    spoiled = f"+spoiled-{args.spoil}" if args.spoil else ""
    prompt = f"file:{args.prompt_file.name}" if args.prompt_file else sj.PROMPT_VERSION
    payload = {
        "prompt_version": prompt + spoiled,
        "reasoning_effort": args.effort or sj.REASONING_EFFORT,
        "home_text": not args.no_home,
        "language": not args.no_language,
        "field": {"other": args.other, "exclude": args.exclude},
        "from_facts": str(args.from_facts) if args.from_facts else None,
        "metrics": metrics(results),
        "failed": failed,
        "scopes": scopes,
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


def _cases(args: argparse.Namespace, golden_dir: str) -> tuple[list[dict[str, Any]], str | None]:
    """Случаи набора — с текстом главной или без — и язык писем набора."""
    letters = args.letters
    if args.golden:
        cases, spec = golden_set(golden_dir, args.manifest or MANIFEST)
        letters = letters or spec.get("letters")
    else:
        cases = load(Path(args.path))
    if args.home is not None:
        limit = sales_cfg.SEGMENT_HOME_TEXT_CHARS if args.home_chars is None else args.home_chars
        cases = with_home(cases, read_homes(args.home), limit)
        print(f"Текст главной: до {limit} знаков из {args.home}")
    if args.no_home:
        cases = without_home(cases)
    return cases, letters


def _system(args: argparse.Namespace) -> str | None:
    """Промпт вместо продуктового: файл (`--prompt-file`) — и/или с порчей (`--spoil`)."""
    prompt = args.prompt_file.read_text(encoding="utf-8").strip() if args.prompt_file else None
    if args.spoil is None:
        return prompt
    return without_rule(prompt or sj.load_prompt(), args.spoil)


def _results(
    cases: list[dict[str, Any]], args: argparse.Namespace, system: str | None, letters: str | None
) -> tuple[list[Result], dict[str, dict[str, Any]]]:
    """Полосы — живой моделью или по фактам прошлого итога (`--from-facts`, модель не зовётся)."""
    policy = policy_of_args(args.other, args.exclude)
    if args.from_facts is not None:
        print(
            f"Полосы по фактам {args.from_facts} — без модели; поле: {args.other}, {args.exclude}"
        )
        return redecided(cases, args.from_facts, policy, letters), saved(args.from_facts)[1]
    product = segment_client.REASONING_EFFORT
    with effort(args.effort) as chosen:
        print(f"Усилие рассуждения: {chosen} (у судьи прогона — {product}); язык писем: {letters}")
        return asyncio.run(_evaluate(cases, args, system, letters))


def _compared(results: list[Result], other: Path | None) -> None:
    """Парно с прошлым итогом на одних сайтах: где права только эта версия, только та, и p."""
    if other is None:
        return
    rows = json.loads(other.read_text(encoding="utf-8"))["results"]
    before = [Result(**{**row, "tags": tuple(row.get("tags") or ())}) for row in rows]
    mine, theirs, p = paired(results, before)
    print(f"Парно с {other.name}: права только эта — {mine}, только та — {theirs}, p = {p:.3f}")


def main(argv: list[str], *, golden_dir: str | None = None) -> int:
    args = _args(argv)
    if args.export_run is not None:
        return export_to(args.export_run, args.out)
    where = sales_cfg.SEGMENT_GOLDEN_DIR if golden_dir is None else golden_dir
    try:
        cases, letters = _cases(args, where)
        if args.fetch_home is not None:
            return fetched_to(cases, args.fetch_home)
        system = _system(args)
    except (SetError, HomesError) as exc:
        print(f"НАБОР НЕ ПРОЧИТАН: {exc}")
        return 1
    if args.spoil is not None:
        print(f"ПОРЧА ПРОМПТА: снято правило «{RULE_WORDS[args.spoil]}» — ворота обязаны закрыться")
    try:
        results, scopes = _results(cases, args, system, letters)
    except FactsError as exc:
        print(f"ФАКТЫ НЕ ПРОЧИТАНЫ: {exc}")
        return 1
    failed = report(results, Gates(), scopes)
    _compared(results, args.paired)
    _written(args, results, failed, scopes)
    if args.spoil is not None:
        print(_spoil_caught(args.spoil, failed))
    if failed:
        print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
