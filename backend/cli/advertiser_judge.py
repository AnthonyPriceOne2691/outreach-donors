"""Проход судьи рекламодателей в тени: `outreach advertisers-judge`.

Главное свойство то же, что у прогона и досуда тематики: **план называется до траты.** Команда
показывает, скольких кандидатов рассудит, сколько из них в случайной выборке, сколько пропущено и
почему, сколько это токенов модели — и ждёт подтверждения. `--dry-run` — только план: модель не
зовётся, страницы не открываются, в базе ничего не меняется.

Проход в тени: пишет только строки суда, решение человека и очередь не трогает. `--sample` — только
строки случайной выборки: на них калибровка судьи (по слову владельца, расход — в журнале).
`--per-donor N` — набор по донорам: не больше N новых случаев на донора, по всем донорам, — чтобы
калибровка видела все площадки и ниши, а не одного донора с сотнями спорных. Прерванный
проход продолжается повтором той же команды: каждый суд записан сразу со строкой журнала расхода, и
пары со свежим судом той же когорты план уже не берёт. Задана вторая модель
(`LLM_ADVERTISER_JUDGE_SECOND_MODEL`) — «не понять» перепроверяет она, вторым кругом: в плане и в итоге
он своей строкой (`crawl/judge_second.py`).

`--relabel` — пересчёт совета строк суда по их сохранённым фактам, без модели и страниц: нынешней
таблицей правил и нынешним признаком донора-каталога (`judge_store.relabel`). Переписываются только
строки, которым пересчёт что-то меняет: совет или правило стали другими — после правки таблицы или
смены признака у донора — либо строка помечена прежней версией таблицы. Остальные не трогаются,
строка с фактами не той формы — мимо, срок фактов пересчёт не сдвигает. План тот же порядком:
сколько строк и какой совет на какой меняется («совет тот же» — сменилось только правило или версия
таблицы), подтверждение, запись. Тот же пересчёт идёт и сам — у ждущих пар перед применением советов
(`judge_apply.py`) и целиком в ночном шаге судьи (`judge_night.py`): команда — для пересчёта руками.
"""

from __future__ import annotations

import argparse
from collections import Counter

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.model_clients import model_and_sites
from backend.features.core.domain import Verdict
from backend.features.crawl.judge_client import JudgeClient
from backend.features.crawl.judge_pass import (
    JUDGED,
    JudgePlan,
    PassReport,
    plan_pass,
    report_lines,
    run_pass,
)
from backend.features.crawl.judge_rules import ADVICE_WORDS, RULES_VERSION, Advice
from backend.features.crawl.judge_store import relabel, relabel_moves
from backend.shared.numbers import spaced

EXIT_OK = 0
EXIT_DECLINED = 1

#: Строка на прерывание (`cli/main._KEPT_ON_INTERRUPT`).
KEPT_ON_INTERRUPT = (
    "Каждый суд записан сразу вместе со строкой расхода: рассуженное осталось в базе, потерян "
    "только случай, который летел. Повтор команды возьмёт остальных."
)


def _confirm(question: str = "Запустить проход судьи?") -> bool:
    try:
        answer = input(f"\n{question} [y/N] ").strip().lower()
    except EOFError:
        # Неинтерактивный запуск без --yes: молча потратить нельзя.
        print("\nНет терминала для подтверждения. Для автоматического запуска — --yes.")
        return False
    return answer in {"y", "yes", "д", "да"}


async def cmd_advertisers_judge(args: argparse.Namespace) -> int:
    async with model_and_sites() as (session, model_http, site_http):
        if args.relabel:
            return await run_relabel(session, args)
        client = JudgeClient(model_http)
        return await run_advertisers_judge(session, args, client=client, http=site_http)


async def run_relabel(session: AsyncSession, args: argparse.Namespace) -> int:
    """Совет строк заново нынешней таблицей: что изменится, подтверждение, запись. Без модели."""
    moves = await relabel_moves(session)
    print_moves(moves)
    if args.dry_run:
        print("\nЭто план: в базе ничего не изменилось.")
        return EXIT_OK
    if not moves:
        return EXIT_OK
    if not args.yes and not _confirm("Пересчитать советы?"):
        print("Не пересчитывали.")
        return EXIT_DECLINED
    changed = await relabel(session)
    await session.commit()
    print(f"Пересчитано строк: {spaced(changed)}, токенов: 0")
    return EXIT_OK


def print_moves(moves: Counter[tuple[str, str]]) -> None:
    print(f"Пересчёт советов таблицей {RULES_VERSION}: строк {spaced(sum(moves.values()))}")
    for (was, now), count in sorted(moves.items()):
        if was != now:
            print(f"  {ADVICE_WORDS[Advice(was)]} → {ADVICE_WORDS[Advice(now)]}: {spaced(count)}")
    same = sum(count for (was, now), count in moves.items() if was == now)
    if same:
        print(f"  совет тот же: {spaced(same)}")
    print("  в тени: пишутся только строки суда, решения человека не трогаются")


async def run_advertisers_judge(
    session: AsyncSession, args: argparse.Namespace, *, client: JudgeClient, http: httpx.AsyncClient
) -> int:
    """План, подтверждение, проход, итог. Клиенты — снаружи: модель и сайты."""
    verdicts = JUDGED if args.verdict is None else (Verdict(args.verdict),)
    plan = await plan_pass(
        session,
        verdicts=verdicts,
        sample_only=args.sample,
        limit=args.limit,
        per_donor=args.per_donor,
        again=args.again,
        model=client.model,
    )
    print_plan(plan)
    if args.dry_run:
        print("\nЭто план: модель не звали, страницы не открывали, в базе ничего не изменилось.")
        return EXIT_OK
    if not plan.ids:
        return EXIT_OK
    if not args.yes and not _confirm():
        print("Не запускали.")
        return EXIT_DECLINED
    report = await run_pass(
        session,
        plan,
        client=client,
        http=http,
        progress=lambda done, total: print(f"  {done} из {total}", flush=True),
    )
    print_report(report)
    return EXIT_OK


def print_plan(plan: JudgePlan) -> None:
    verdicts = ", ".join(
        f"{name} {spaced(count)}" for name, count in sorted(plan.by_verdict.items())
    )
    print(
        f"Судья рекламодателей, модель {plan.cohort.model}, подсказка {plan.cohort.prompt_version}"
    )
    print(
        f"  к суду: {spaced(len(plan.ids))} ({verdicts or 'никого'}), из них в выборке "
        f"{spaced(plan.sampled)}, доноров {spaced(plan.donors)}"
    )
    if plan.fresh:
        print(
            f"  уже судимы (свежий суд нынешней когорты), страницы не открываем: {spaced(plan.fresh)}"
        )
    if plan.without_page:
        print(f"  пропущено без лучшей страницы: {spaced(plan.without_page)}")
    source = "по истории когорты" if plan.from_history else "запасное число, истории ещё нет"
    print(f"  токенов: до {spaced(plan.tokens)} — {spaced(plan.per_case)} на случай, {source}")
    if plan.second is not None and plan.second.pairs:
        print(f"  в том числе — {plan.second.line()}")
    print("  в тени: пишутся только строки суда, решения человека не трогаются")


def print_report(report: PassReport) -> None:
    print(f"\nИтог: случаев {spaced(report.done)}, токенов {spaced(report.tokens)}")
    for line in report_lines(report):
        print(f"  {line}")
    for kind, count in sorted(report.refusals.items()):
        print(f"  отказ «{kind}»: {spaced(count)}")
    if report.stopped:
        print(f"\nОстановлено: {report.stopped}. Не спрошены: {spaced(report.left)}.")


def _positive(text: str) -> int:
    """`--limit` — целое от единицы."""
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"нужно целое число от 1, а не «{text}»")
    return int(text)


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "advertisers-judge",
        help="судья рекламодателей в тени: факты о спорных и «куплено», совет — без применения",
    )
    parser.add_argument(
        "--sample", action="store_true", help="только строки случайной выборки (калибровка)"
    )
    parser.add_argument(
        "--verdict",
        choices=[verdict.value for verdict in JUDGED],
        default=None,
        help="один вердикт",
    )
    parser.add_argument(
        "--limit", type=_positive, default=None, help="сколько случаев, выборка первой"
    )
    parser.add_argument(
        "--per-donor",
        type=_positive,
        default=None,
        help="набор по донорам: не больше N новых случаев на донора, по всем донорам",
    )
    parser.add_argument(
        "--again", action="store_true", help="заново и тем, у кого свежий суд той же когорты"
    )
    parser.add_argument(
        "--relabel",
        action="store_true",
        help="совет заново нынешней таблицей по сохранённым фактам: без модели и страниц",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="только план: сколько и токены; без траты"
    )
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждения")
