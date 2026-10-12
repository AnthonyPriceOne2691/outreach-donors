"""Оценка вида ответа продаж на наборе: полнота главных видов и опасные ошибки.

Запуск:

    python scripts/eval_sales_reply.py                          # синтетика из репозитория
    python scripts/eval_sales_reply.py --golden                 # внешний набор: $SALES_GOLDEN_DIR
    python scripts/eval_sales_reply.py --drop-kind wants_to_talk  # порча промпта — обязан покраснеть
    python scripts/eval_sales_reply.py --golden --services "SEO-аудит;Ссылки"  # услуги базы

Синтетика — `scripts/data/sales_reply_synthetic.jsonl`: выдуманные ответы по
видам, на девяти языках, с инъекцией. Настоящих писем в репозитории нет
намеренно — он публичный: обезличенный набор из переписки лежит вне его
(`SALES_GOLDEN_DIR`), здесь — только манифест (`sales_reply_golden.manifest.json`):
число примеров, хэш, дата, базовая метрика. Набор не нашёлся или не тот,
что в манифесте, — красный, а не «ноль расхождений».

**Ворота — предложение, решает владелец** (порог задаётся решением, а не
ожиданием): полнота `wants_to_talk` не ниже 0,95; полнота `unsubscribe` — 1,0;
опасных — 0. Опасный — «хочет говорить» или «просит не писать», ушедший
не туда без человека: модель назвала другой вид с уверенностью не ниже
порога (`SALES_REPLY_CONFIDENCE`). Ниже порога ответ ждёт человека — это
минута человека, а не потеря. Отдельно — ложная отписка: чужой вид, принятый
за «не пишите» без человека, закрыл бы адрес во всех направлениях.

**Порча промпта** (`--drop-kind`) снимает из промпта строку определения вида —
обратный прогон ворот: на живой модели eval обязан покраснеть. Запись прогона —
в verify-report среза; механика — `tests/test_sales_reply_eval.py`.

**Поля v2** (`sales-reply-kind-v2`: имя названного, проект, услуги) меряются рядом с видами,
но вне ворот — их пороги владелец решит по живому прогону: имя названного — верно ли, проект —
найден ли (слова — из письма, это держит код), услуги — те ли из списка базы. Список услуг
синтетики — выдуманный (`SYNTHETIC_SERVICES`), у внешнего набора — `--services` (названия
записей «услуга» базы через «;»). Прежние числа видов меряются как раньше.

Модель зовётся по-настоящему — нужен `LLM_API_KEY`; запускает координатор
по слову владельца.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import sales as sales_cfg
from backend.features.sales import reply_kind
from backend.features.sales.reply_kind import KindClient, KindFound, SalesKind, Unanswered
from backend.features.sales.reply_routes import Route, decide

DATA = Path(__file__).parent / "data"
SYNTHETIC = DATA / "sales_reply_synthetic.jsonl"
MANIFEST = DATA / "sales_reply_golden.manifest.json"
GOLDEN_ENV = "SALES_GOLDEN_DIR"
#: Виды, ошибка в которых опасна: человеку не позвонили или написали тому, кто просил не писать.
GUARDED = (SalesKind.WANTS_TO_TALK, SalesKind.UNSUBSCRIBE)
#: Услуги выдуманной базы знаний: из них модель выбирает услуги ответа синтетики.
SYNTHETIC_SERVICES = ("SEO audit", "Link building", "Technical SEO")


class SetError(RuntimeError):
    """Набор не прочитан: нет файла, не тот хэш или число примеров."""


@dataclass(frozen=True, slots=True)
class Gates:
    wants_recall: float = 0.95
    unsubscribe_recall: float = 1.0
    dangerous: int = 0
    false_unsubscribe: int = 0


@dataclass(frozen=True, slots=True)
class Result:
    case_id: str
    want: SalesKind
    got: SalesKind | None
    confidence: float
    route: Route
    want_contact: str | None = None
    got_contact: str | None = None
    refusal: str | None = None
    #: Поля v2: чего ждали и что вышло после сверки кодом.
    want_name: str | None = None
    got_name: str | None = None
    want_project: bool = False
    got_project: str | None = None
    want_services: tuple[str, ...] = ()
    got_services: tuple[str, ...] = ()

    @property
    def auto(self) -> bool:
        """Ответ ушёл своим путём без человека (уверенность не ниже порога)."""
        return self.route is not Route.MANUAL

    @property
    def dangerous(self) -> bool:
        return self.want in GUARDED and self.got is not self.want and self.auto

    @property
    def false_unsubscribe(self) -> bool:
        return (
            self.want is not SalesKind.UNSUBSCRIBE
            and self.got is SalesKind.UNSUBSCRIBE
            and self.auto
        )


def load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SetError(f"набор не найден: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def golden(directory: str | None, manifest: Path = MANIFEST) -> list[dict[str, Any]]:
    """Внешний набор по манифесту: файл, число примеров и хэш обязаны совпасть."""
    if not directory:
        raise SetError(f"набор не найден: задайте {GOLDEN_ENV} — каталог вне репозитория")
    spec = json.loads(manifest.read_text(encoding="utf-8"))
    path = Path(directory) / spec["file"]
    cases = load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"Набор {path}: примеров {len(cases)}, sha256 {digest}")
    if spec.get("sha256") and spec["sha256"] != digest:
        raise SetError(f"набор не тот, что в манифесте: sha256 {digest} ≠ {spec['sha256']}")
    if spec.get("count") and spec["count"] != len(cases):
        raise SetError(f"набор не тот, что в манифесте: примеров {len(cases)} ≠ {spec['count']}")
    return cases


def without_kind(prompt: str, kind: str) -> str:
    """Промпт без строки определения вида — порча для обратного прогона."""
    marker = f'- "{kind}":'
    kept = [line for line in prompt.splitlines() if not line.lstrip().startswith(marker)]
    if len(kept) == len(prompt.splitlines()):
        raise SetError(f"в промпте нет определения вида {kind} — портить нечего")
    return "\n".join(kept)


def judged(case: dict[str, Any], found: KindFound | Unanswered, threshold: float) -> Result:
    expect = case["expect"]
    want = SalesKind(expect["kind"])
    contact = expect.get("contact")
    wanted = {
        "want_name": expect.get("contact_name"),
        "want_project": bool(expect.get("project")),
        "want_services": tuple(expect.get("services") or ()),
    }
    if isinstance(found, Unanswered):
        return Result(
            case["id"], want, None, 0.0, Route.MANUAL, contact, refusal=found.reason, **wanted
        )
    route = decide(found, threshold).route
    return Result(
        case["id"],
        want,
        found.kind,
        found.confidence,
        route,
        contact,
        found.contact,
        got_name=found.contact_name,
        got_project=found.project,
        got_services=found.services,
        **wanted,
    )


async def run(
    cases: Iterable[dict[str, Any]],
    kinds: KindClient,
    threshold: float,
    services: Sequence[str] = SYNTHETIC_SERVICES,
) -> tuple[list[Result], int]:
    results: list[Result] = []
    tokens = 0
    for case in cases:
        found = await kinds.classify(
            text=case["text"], subject=case.get("subject", "Re:"), services=services
        )
        tokens += found.tokens if isinstance(found, KindFound) else 0
        results.append(judged(case, found, threshold))
    return results, tokens


def _same(got: str | None, want: str | None) -> bool:
    return " ".join((got or "").lower().split()) == " ".join((want or "").lower().split())


def fields(results: list[Result]) -> list[str]:
    """Поля v2 словами — вне ворот: имя названного, проект, услуги; чего ждали и что лишнее."""
    named = [r for r in results if r.want_name]
    names = sum(_same(r.got_name, r.want_name) for r in named)
    extra_names = [r.case_id for r in results if r.got_name and not r.want_name]
    described = [r for r in results if r.want_project]
    projects = sum(r.got_project is not None for r in described)
    extra_projects = [r.case_id for r in results if r.got_project and not r.want_project]
    asked = [r for r in results if r.want_services]
    services = sum(set(r.got_services) == set(r.want_services) for r in asked)
    extra_services = [r.case_id for r in results if r.got_services and not r.want_services]
    return [
        f"Поля v2 (вне ворот): имя названного верно {names}/{len(named)}, "
        f"лишнее {len(extra_names)} {extra_names or ''}",
        f"  проект найден {projects}/{len(described)}, "
        f"лишний {len(extra_projects)} {extra_projects or ''}",
        f"  услуги верно {services}/{len(asked)}, "
        f"лишние {len(extra_services)} {extra_services or ''}",
    ]


def recall(results: list[Result], kind: SalesKind) -> float | None:
    wanted = [r for r in results if r.want is kind]
    return sum(r.got is kind for r in wanted) / len(wanted) if wanted else None


def report(results: list[Result], tokens: int, gates: Gates) -> list[str]:
    """Печать итога. Возвращает нарушенные ворота словами — пусто, если зелёный."""
    total = len(results)
    right = sum(r.got is r.want for r in results)
    print(f"Версия промпта: {reply_kind.PROMPT_VERSION}, примеров {total}, токенов {tokens}")
    print(f"Вид верно: {right}/{total}; ушли человеку: {sum(not r.auto for r in results)}/{total}")
    for kind in SalesKind:
        share = recall(results, kind)
        if share is not None:
            print(f"  полнота {kind.value:<15} {share:.0%}")
    wrong_contacts = [
        r.case_id for r in results if r.want_contact and r.got_contact != r.want_contact
    ]
    print(f"Адрес «другого человека» не тот: {len(wrong_contacts)} {wrong_contacts or ''}")
    print(*fields(results), sep="\n")
    print(f"Отказов модели: {sum(r.refusal is not None for r in results)}")
    print(
        f"По видам (ждали → назвала): {dict(Counter(f'{r.want}→{r.got}' for r in results if r.got is not r.want))}"
    )
    dangerous = [r.case_id for r in results if r.dangerous]
    false_unsub = [r.case_id for r in results if r.false_unsubscribe]
    print(
        f"ОПАСНЫХ: {len(dangerous)} {dangerous or ''}; ложных отписок: {len(false_unsub)} {false_unsub or ''}"
    )
    failed = []
    wants = recall(results, SalesKind.WANTS_TO_TALK)
    if wants is not None and wants < gates.wants_recall:
        failed.append(f"полнота wants_to_talk {wants:.0%} < {gates.wants_recall:.0%}")
    unsub = recall(results, SalesKind.UNSUBSCRIBE)
    if unsub is not None and unsub < gates.unsubscribe_recall:
        failed.append(f"полнота unsubscribe {unsub:.0%} < {gates.unsubscribe_recall:.0%}")
    if len(dangerous) > gates.dangerous:
        failed.append(f"опасных {len(dangerous)} > {gates.dangerous}")
    if len(false_unsub) > gates.false_unsubscribe:
        failed.append(f"ложных отписок {len(false_unsub)} > {gates.false_unsubscribe}")
    return failed


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", nargs="?", default=str(SYNTHETIC), help="набор JSONL")
    parser.add_argument("--golden", action="store_true", help=f"внешний набор из ${GOLDEN_ENV}")
    parser.add_argument("--drop-kind", choices=sorted(k.value for k in SalesKind), default=None)
    parser.add_argument("--threshold", type=float, default=sales_cfg.REPLY_CONFIDENCE)
    parser.add_argument("--model", default=None, help="модель вместо пина LLM_SALES_CLASSIFY_MODEL")
    parser.add_argument(
        "--services",
        default=None,
        help="названия записей «услуга» базы знаний через «;» — по умолчанию выдуманные синтетики",
    )
    return parser.parse_args(argv)


def known_services(raw: str | None) -> tuple[str, ...]:
    """Список услуг для разбора: из `--services` или выдуманный синтетики."""
    if raw is None:
        return SYNTHETIC_SERVICES
    return tuple(name.strip() for name in raw.split(";") if name.strip())


async def _evaluate(
    cases: list[dict[str, Any]], args: argparse.Namespace
) -> tuple[list[Result], int]:
    kinds = KindClient(model=args.model)
    try:
        return await run(cases, kinds, args.threshold, known_services(args.services))
    finally:
        await kinds.aclose()


def main(argv: list[str], *, golden_dir: str | None = None) -> int:
    args = _args(argv)
    where = sales_cfg.GOLDEN_DIR if golden_dir is None else golden_dir
    try:
        cases = golden(where) if args.golden else load(Path(args.path))
        if args.drop_kind:
            spoiled = without_kind(reply_kind.load_prompt(), args.drop_kind)
            reply_kind.load_prompt = lambda: spoiled  # type: ignore[method-assign]
            print(f"ПОРЧА ПРОМПТА: снято определение «{args.drop_kind}» — ворота обязаны закрыться")
    except SetError as exc:
        print(f"НАБОР НЕ ПРОЧИТАН: {exc}")
        return 1
    results, tokens = asyncio.run(_evaluate(cases, args))
    failed = report(results, tokens, Gates())
    if failed:
        print("\nВОРОТА ЗАКРЫТЫ: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
