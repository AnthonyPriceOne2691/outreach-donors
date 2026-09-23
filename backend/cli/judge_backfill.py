"""Досуд базы, собранной до судьи: `outreach judge-backfill`.

Главное свойство то же, что у прогона: **план называется до траты.**
Команда показывает, скольких будет судить и откуда возьмёт текст, и
ждёт подтверждения. Метрики Ahrefs не покупаются вовсе — платят токенами
модели и центами за повторную выдачу, и обе строки уходят в журнал.
"""

from __future__ import annotations

import argparse

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.donors.backfill import (
    BackfillPlan,
    BackfillReport,
    backfill,
    plan_backfill,
)
from backend.features.serp.factory import build_provider
from backend.shared.net.url_guard import guarded_client


def _confirm() -> bool:
    try:
        answer = input("\nСудить? [y/N] ").strip().lower()
    except EOFError:
        # Неинтерактивный запуск без --yes: молча потратить нельзя.
        print("\nНет терминала для подтверждения. Для автоматического запуска — --yes.")
        return False
    return answer in {"y", "yes", "д", "да"}


async def cmd_judge_backfill(args: argparse.Namespace) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            plan = await plan_backfill(session, limit=args.limit, rejudge=args.rejudge)
            _print_plan(plan, no_serp=args.no_serp)
            if not plan.hosts:
                return 0
            if not args.yes and not _confirm():
                print("Не судили.")
                return 1

            provider = None if args.no_serp else build_provider(AhrefsClient())
            async with (
                httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http,
                guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC) as home,
            ):
                report = await backfill(
                    session,
                    plan,
                    http=http,
                    home_client=home,
                    provider=provider,
                    progress=lambda done, total: print(f"  судили {done} из {total}", flush=True),
                )
    finally:
        await engine.dispose()

    _print_report(report)
    return 0


def _print_plan(plan: BackfillPlan, *, no_serp: bool) -> None:
    missing = len(plan.hosts) - len(plan.saved)
    print(f"Судить: {len(plan.hosts)}")
    if plan.skipped_reserved:
        print(f"  выдуманных (.test) пропущено: {plan.skipped_reserved}")
    print(f"  текст в сохранённой выдаче: {len(plan.saved)}")
    print(f"  без текста: {missing}")
    if missing and plan.searches and not no_serp:
        print(
            f"  выдача заново: {plan.keywords_to_search} ключей из {len(plan.searches)} "
            "прогонов — центы у источника выдачи, Ahrefs не трогается"
        )
    print("  кого не найдём и там — по главной, с пометкой в причине")


def _print_report(report: BackfillReport) -> None:
    judge = report.judge
    print("\n── Досуд ──")
    for source, count in report.by_source.items():
        print(f"  текст: {source:<20} {count}")
    if report.no_text:
        print(f"  текста не нашлось нигде: {report.no_text} — остались без вердикта")
    print(f"  судили {judge.judged}, отрезал бы {judge.would_cut}, к человеку {judge.to_review}")
    for who, count in sorted(judge.by_decider.items(), key=lambda kv: -kv[1]):
        print(f"    решено {who:<10} {count}")
    if judge.from_index:
        print(f"  главная закрыта, судил по индексу: {judge.from_index}")
    if judge.home_unreached:
        print(f"  главная не открылась и в индексе нет: {judge.home_unreached}")
    print(f"  токенов {judge.tokens:,}, выдача ${report.serp_cost_usd:.3f}".replace(",", " "))
    print("\nРезультат — на экране «Отбор», фильтр «Судья не смотрел» должен опустеть.")


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "judge-backfill", help="досудить базу, собранную до судьи (токены, не юниты)"
    )
    parser.add_argument("--limit", type=int, default=None, help="сколько доменов, по DR сверху")
    parser.add_argument(
        "--no-serp", action="store_true", help="не повторять выдачу — только сохранённое и главные"
    )
    parser.add_argument(
        "--rejudge",
        action="store_true",
        help="пересудить и тех, у кого вердикт уже есть (судья улучшился); человека не трогает",
    )
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждения")
