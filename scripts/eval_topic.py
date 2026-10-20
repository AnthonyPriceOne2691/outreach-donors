"""Оценка тематики сайта на наборе: верна ли основная тема, попала ли тема в две, по языкам.

Запуск:

    python scripts/eval_topic.py --answers tests/test_topic_eval_answers.json   # без сети
    python scripts/eval_topic.py                       # живая модель: нужен LLM_API_KEY
    python scripts/eval_topic.py --out report.json     # итог ещё и файлом
    python scripts/eval_topic.py --effort minimal      # другое усилие рассуждения — замер

**Живой прогон — по слову владельца:** это вызовы модели за деньги (52 случая ≈ 104 тыс.
токенов и ≈ 0,05 $ по оценке сметы, `topics/cost.py`). Без `--answers` скрипт зовёт модель
так же, как прогон (`classifier.classify`): тот же промпт, та же модель `LLM_TOPIC_MODEL`.

**Набор** — `scripts/data/topic_golden.jsonl`: синтетические сайты на шести языках, хосты —
в зоне `.example` (репозиторий публичный). Случай: `id`, `lang`, `host`, `expect` — код темы
или `none` (по тексту темы не понять), `also` — другие допустимые основные темы, страница из
выдачи `serp` и главная `home` (или `null`), `trap` — на чём модель вероятнее ошибётся.
Случаи «только заголовок» (`serp: null`, у главной — один `title`) — то, что досуд читает
у домена, чью главную не открыл: заголовок, сохранённый судом. Текст для модели собирается
тем же `classifier.site_text`, что в прогоне. Набор меньше 40 случаев или пяти языков —
отказ, а не «точность 100 %» на трёх сайтах.

**Метрики:** основная верна — тема модели равна `expect` или из `also` (для `none` — модель
прочла и сказала `null`, `TopicVerdict.unclear`); основная или вторая — `expect` среди двух
тем модели; по языкам — основная. «Не ответила» и «ответ не по форме» (проза, обрезан, тема
не из списка) — своими числами с первой причиной: это не ошибка темы, но и не попадание, и
сбой формы на случае `none` не засчитывается за «не понять». Токены — всего, ввод и вывод.

**Ворота — предложение, решает владелец:** основная — не ниже 80 %, основная или вторая —
не ниже 90 %, на каждом языке основная — не ниже 70 %, «не ответила» и «не по форме» — ноль.

**Записанные ответы** (`--answers`, файл `{id случая: ответ модели}`) написаны руками для
проверки механики оценки и ворот — это не замер модели. Так его гоняет тест
`tests/test_topic_eval.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from backend.config import llm as llm_cfg
from backend.config import topics as topics_cfg
from backend.features.donors.home_signals import HomeSignals
from backend.features.runs.planning import SerpText
from backend.features.topics.classifier import (
    TOPIC_VERSION,
    SiteText,
    TopicVerdict,
    site_text,
)
from backend.features.topics.step import classify_many
from backend.features.topics.taxonomy import BY_CODE, NOT_DEFINED

DATA = Path(__file__).parent / "data" / "topic_golden.jsonl"
MIN_CASES = 40
MIN_LANGS = 5
EFFORTS = ("minimal", "low", "medium", "high")
#: Токены записанного ответа: ввод — промпт и текст, вывод — ответ с рассуждением.
RECORDED_USAGE = {"prompt_tokens": 1250, "completion_tokens": 250, "total_tokens": 1500}

EXIT_PASSED = 0
EXIT_GATES = 1
EXIT_SET = 2


class SetError(RuntimeError):
    """Набор не прочитан: нет файла, мал, битый случай."""


@dataclass(frozen=True, slots=True)
class Gates:
    primary_min: float = 0.80
    either_min: float = 0.90
    per_language_min: float = 0.70


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    lang: str
    host: str
    expect: str
    also: tuple[str, ...]
    text: SiteText
    trap: str = ""


@dataclass(frozen=True, slots=True)
class Result:
    id: str
    lang: str
    expect: str
    topic: str | None
    secondary: str | None
    reason: str
    unanswered: bool
    unreadable: bool
    tokens: int
    prompt_tokens: int
    completion_tokens: int
    primary_right: bool
    either_right: bool
    trap: str = ""


@dataclass(slots=True)
class Report:
    version: str
    cases: int
    primary: float
    either: float
    by_language: dict[str, float]
    unanswered: int
    unanswered_reason: str | None
    unreadable: int
    unreadable_reason: str | None
    tokens: int
    prompt_tokens: int
    completion_tokens: int
    misses: list[dict[str, Any]] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.gates


def _known(code: str) -> bool:
    return code == NOT_DEFINED or code in BY_CODE


def _home(raw: Any) -> HomeSignals | None:
    if not raw:
        return None
    return HomeSignals(
        reached=True,
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        nav=tuple(str(item) for item in raw.get("nav") or ()),
    )


#: Поля случая, без которых его нет: строкой и непустые.
REQUIRED = ("id", "lang", "host", "expect")


def _case(raw: dict[str, Any], n: int) -> Case:
    empty = [name for name in REQUIRED if not isinstance(raw.get(name), str) or not raw[name]]
    if empty:
        raise SetError(f"строка {n}: случай не разобран — нет {', '.join(empty)}")
    try:
        serp = raw["serp"] or {}
        page = SerpText(
            url=f"https://{raw['host']}/",
            title=serp.get("title"),
            description=serp.get("description"),
        )
        case = Case(
            id=str(raw["id"]),
            lang=str(raw["lang"]),
            host=str(raw["host"]),
            expect=str(raw["expect"]),
            also=tuple(str(code) for code in raw.get("also") or ()),
            text=site_text(page, _home(raw.get("home"))),
            trap=str(raw.get("trap") or ""),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise SetError(f"строка {n}: случай не разобран — {exc!r}") from exc
    unknown = [code for code in (case.expect, *case.also) if not _known(code)]
    if unknown:
        raise SetError(f"строка {n}, {case.id}: темы нет в списке — {', '.join(unknown)}")
    if case.text.empty:
        raise SetError(f"строка {n}, {case.id}: текста для модели нет")
    return case


def load(path: Path) -> list[Case]:
    """Случаи набора — или отказ словами: нет файла, мал, битый случай, повтор."""
    if not path.is_file():
        raise SetError(f"набора нет: {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        cases = [_case(json.loads(line), n) for n, line in enumerate(lines, start=1)]
    except ValueError as exc:
        raise SetError(f"набор не JSONL: {exc}") from exc
    ids = Counter(case.id for case in cases)
    twice = sorted(name for name, count in ids.items() if count > 1)
    if twice:
        raise SetError(f"случаи записаны дважды: {', '.join(twice)}")
    langs = {case.lang for case in cases}
    if len(cases) < MIN_CASES or len(langs) < MIN_LANGS:
        raise SetError(
            f"набор мал: {len(cases)} случаев на {len(langs)} языках — нужно не меньше "
            f"{MIN_CASES} на {MIN_LANGS}"
        )
    return cases


class Recorded:
    """Клиент модели без сети: записанный ответ по домену из запроса."""

    def __init__(self, answers: dict[str, str], cases: list[Case]) -> None:
        by_id = {case.id: case.host for case in cases}
        self._answers = {by_id[key]: value for key, value in answers.items() if key in by_id}

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        payload = kwargs.get("json") or {}
        asked = str(payload["messages"][1]["content"]).splitlines()[0].removeprefix("Домен: ")
        content = self._answers.get(asked)
        if content is None:
            return httpx.Response(503, json={"error": {"message": "нет записи"}}, request=request)
        body = {"choices": [{"message": {"content": content}}], "usage": RECORDED_USAGE}
        return httpx.Response(200, json=body, request=request)


def _judge(case: Case, verdict: TopicVerdict) -> Result:
    if case.expect == NOT_DEFINED:
        # Только явный `null`: проза или тема не из списка — сбой формы, а не «не понять».
        primary = either = verdict.unclear
    else:
        primary = verdict.topic in (case.expect, *case.also)
        either = case.expect in (verdict.topic, verdict.secondary) or primary
    return Result(
        id=case.id,
        lang=case.lang,
        expect=case.expect,
        topic=verdict.topic,
        secondary=verdict.secondary,
        reason=verdict.reason,
        unanswered=verdict.unanswered and not verdict.unreadable,
        unreadable=verdict.unreadable,
        tokens=verdict.tokens,
        prompt_tokens=verdict.prompt_tokens,
        completion_tokens=verdict.completion_tokens,
        primary_right=primary,
        either_right=either,
        trap=case.trap,
    )


async def evaluate(cases: list[Case], http: Any) -> list[Result]:
    """Темы всех случаев тем же путём, что прогон: параллельно, с отказом — «не ответила»."""
    verdicts = await classify_many(http, {case.host: case.text for case in cases})
    return [_judge(case, verdicts[case.host]) for case in cases]


def _share(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def _failed(results: list[Result], kind: str) -> tuple[int, str | None]:
    """Сколько случаев со сбоем `kind` (`unanswered` или `unreadable`) и первая причина."""
    found = [r for r in results if getattr(r, kind)]
    return len(found), (found[0].reason if found else None)


def report(results: list[Result], gates: Gates) -> Report:
    langs = sorted({result.lang for result in results})
    by_language = {
        lang: _share(
            sum(r.primary_right for r in results if r.lang == lang),
            sum(1 for r in results if r.lang == lang),
        )
        for lang in langs
    }
    unanswered, unanswered_reason = _failed(results, "unanswered")
    unreadable, unreadable_reason = _failed(results, "unreadable")
    made = Report(
        version=TOPIC_VERSION,
        cases=len(results),
        primary=_share(sum(r.primary_right for r in results), len(results)),
        either=_share(sum(r.either_right for r in results), len(results)),
        by_language=by_language,
        unanswered=unanswered,
        unanswered_reason=unanswered_reason,
        unreadable=unreadable,
        unreadable_reason=unreadable_reason,
        tokens=sum(r.tokens for r in results),
        prompt_tokens=sum(r.prompt_tokens for r in results),
        completion_tokens=sum(r.completion_tokens for r in results),
        misses=[asdict(r) for r in results if not r.primary_right],
    )
    made.gates = closed(made, gates)
    return made


def closed(made: Report, gates: Gates) -> list[str]:
    """Какие ворота закрыты — словами; пусто — все открыты."""
    found: list[str] = []
    if made.primary < gates.primary_min:
        found.append(f"основная верна в {made.primary:.0%} при допуске {gates.primary_min:.0%}")
    if made.either < gates.either_min:
        found.append(f"основная или вторая — {made.either:.0%} при допуске {gates.either_min:.0%}")
    for lang, share in made.by_language.items():
        if share < gates.per_language_min:
            found.append(f"язык {lang}: {share:.0%} при допуске {gates.per_language_min:.0%}")
    if made.unanswered:
        found.append(f"модель не ответила по {made.unanswered}: {made.unanswered_reason}")
    if made.unreadable:
        found.append(f"ответ не по форме у {made.unreadable}: {made.unreadable_reason}")
    return found


def show(made: Report) -> None:
    print(f"Версия промпта: {made.version}, случаев {made.cases}")
    print(f"Основная тема верна: {made.primary:.1%}; основная или вторая: {made.either:.1%}")
    print(
        "По языкам: " + ", ".join(f"{lang} {share:.0%}" for lang, share in made.by_language.items())
    )
    print(
        f"Токены: {made.tokens} (ввод {made.prompt_tokens}, вывод {made.completion_tokens}); "
        f"на случай ≈ {made.tokens // max(1, made.cases)}"
    )
    if made.unanswered:
        print(f"Модель не ответила: {made.unanswered} — {made.unanswered_reason}")
    if made.unreadable:
        print(f"Ответ не по форме: {made.unreadable} — {made.unreadable_reason}")
    for miss in made.misses:
        got = miss["topic"] or "—"
        if miss["secondary"]:
            got += f" + {miss['secondary']}"
        trap = f"  [{miss['trap']}]" if miss["trap"] else ""
        print(
            f"  ✗ {miss['id']:<22} ждали {miss['expect']:<12} дала {got:<24} {miss['reason'][:60]}{trap}"
        )
    print("\nВОРОТА ОТКРЫТЫ" if made.passed else "\nВОРОТА ЗАКРЫТЫ: " + "; ".join(made.gates))


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(DATA), help="набор JSONL")
    parser.add_argument("--answers", help="записанные ответы {id: ответ} — без сети")
    parser.add_argument("--out", help="итог ещё и файлом JSON")
    parser.add_argument("--effort", choices=EFFORTS, help="усилие рассуждения вместо продуктового")
    return parser.parse_args(argv)


async def _run(cases: list[Case], answers: dict[str, str] | None) -> list[Result]:
    if answers is not None:
        return await evaluate(cases, Recorded(answers, cases))
    async with httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http:
        return await evaluate(cases, http)


def main(argv: list[str] | None = None) -> int:
    args = _args(sys.argv[1:] if argv is None else argv)
    try:
        cases = load(Path(args.path))
    except SetError as exc:
        print(f"Набор не принят: {exc}")
        return EXIT_SET
    if args.effort:
        topics_cfg.REASONING_EFFORT = args.effort
    answers = None
    if args.answers:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        # Ключ — только чтобы запрос собрался: записанный клиент в сеть не ходит.
        llm_cfg.API_KEY = llm_cfg.API_KEY or "recorded"
    made = report(asyncio.run(_run(cases, answers)), Gates())
    show(made)
    if args.out:
        Path(args.out).write_text(json.dumps(asdict(made), ensure_ascii=False, indent=2), "utf-8")
    return EXIT_PASSED if made.passed else EXIT_GATES


if __name__ == "__main__":
    sys.exit(main())
