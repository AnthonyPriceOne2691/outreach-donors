"""Запуск прогона из командной строки.

Главное свойство: **стоимость называется до того, как её потратят.** Прогон
показывает смету и остаток и ждёт подтверждения. Автоматический запуск тоже
возможен (`--yes`), но это осознанный выбор вызывающего, а не умолчание.

Про коды возврата. Каждый вид отказа имеет свой код, чтобы вызывающий скрипт
мог различить «не хватило остатка» и «провайдер недоступен», не разбирая текст.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.cli.access_admin import cmd_user_add, cmd_user_reset
from backend.cli.contact_search import cmd_contacts
from backend.cli.demo_data import cmd_demo_seed
from backend.cli.keywords_pool import add_parser as add_keywords_parser
from backend.cli.keywords_pool import cmd_keywords
from backend.cli.letters_queue import add_parser as add_letters_parser
from backend.cli.letters_queue import cmd_letters, cmd_letters_build, cmd_letters_send
from backend.config import ahrefs as ahrefs_cfg
from backend.config import filters, storage
from backend.config.startup_checks import ConfigError, check_collect, check_storage
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.donors.repository import DonorRepository
from backend.features.runs.budget import (
    CapExceededError,
    QuotaUnavailableError,
    units_left,
)
from backend.features.runs.pipeline import (
    RunDeps,
    RunPlan,
    RunReport,
    RunRequest,
    execute_run,
    gather_candidates,
    plan_run,
)
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import cap_left
from backend.features.runs.thresholds import defaults
from backend.features.serp.factory import UnknownProviderError, build_provider
from backend.shared.logs import setup_logging

EXIT_OK = 0
EXIT_MISCONFIGURED = 2
EXIT_CAP_EXCEEDED = 3
EXIT_QUOTA_UNAVAILABLE = 4
EXIT_PROVIDER_FAILED = 5
EXIT_CANCELLED = 6


def _read_keywords(path: Path) -> list[str]:
    if not path.exists():
        raise ConfigError(f"Файл с ключевыми словами не найден: {path}")
    words = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    words = [w for w in words if w]
    if not words:
        raise ConfigError(f"В файле {path} нет ни одного ключевого слова")
    return words


async def cmd_quota() -> int:
    """Остаток юнитов. Запрос бесплатный."""
    client = AhrefsClient()
    try:
        quota = Quota.from_payload(await client.limits_and_usage())
    finally:
        await client.aclose()

    print(f"Доступно юнитов:     {quota.available:,}".replace(",", " "))
    print(f"  ключ:              {quota.key_used:,} из {quota.key_limit:,}".replace(",", " "))
    print(
        f"  пространство:      {quota.workspace_used:,} из {quota.workspace_limit:,}".replace(
            ",", " "
        )
    )
    if quota.reset_date:
        print(f"  обнуление:         {quota.reset_date}")
    print("\nЛимита два и действуют одновременно — доступен меньший остаток.")
    return EXIT_OK


def _print_plan(plan: RunPlan, budget: int) -> None:
    p = plan
    candidates = p.candidates
    print(f"\nКлючей:              {candidates.keywords}")
    print(f"Результатов выдачи:  {candidates.results}")
    print(f"Уникальных доменов:  {len(candidates.hosts)}")
    print(f"  схлопнуто дублей:  {candidates.duplicates}")
    if candidates.dropped:
        print(f"  не разобрано:      {candidates.dropped}")
    if candidates.empty_keywords:
        print(f"  ключей без выдачи: {len(candidates.empty_keywords)}")
    print(f"\nУже проверены:       {len(p.fresh)}  (платить не нужно)")
    print(f"Проверить сейчас:    {len(p.new)}")
    estimate = p.estimate
    print(f"\nСмета:               {estimate.total:,} юнитов".replace(",", " "))
    print(f"  просев по DR:      {estimate.screen:,}".replace(",", " "))
    print(f"  метрики:           {estimate.metrics:,}".replace(",", " "))
    print(f"  страны:            {estimate.by_country:,}".replace(",", " "))
    print(f"Доступно:            {budget:,} юнитов".replace(",", " "))
    if p.savings_from_cache:
        print(f"Сэкономлено кэшем:   {p.savings_from_cache:,} юнитов".replace(",", " "))


async def cmd_run(args: argparse.Namespace) -> int:
    """Прогон: смета, подтверждение, сбор."""
    check_storage()
    check_collect()
    # Настройки должны позволять запрашивать страны с лимитом — иначе
    # фильтр начнёт молча отсеивать подходящие домены.
    assert_settings_allow_limited_fetch()

    keywords = _read_keywords(Path(args.keywords))
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AhrefsClient()
    provider = build_provider(client)

    try:
        async with factory() as session:
            donors = DonorRepository(session)
            runs = RunRepository(session)

            candidates = await gather_candidates(
                provider, keywords, args.country, depth_pages=args.depth
            )
            # Кап месячный: из него вычитается уже потраченное нами,
            # иначе он ограничивает один прогон, а не месяц. Свой
            # потолок (`--cap`) может быть только меньше.
            month_left = await cap_left(session, cap=ahrefs_cfg.UNITS_CAP)
            allowed = min(args.cap, month_left) if args.cap else month_left
            budget = await units_left(client, cap=allowed)
            plan = await plan_run(candidates, donors, units_left=budget)
            _print_plan(plan, budget)

            if not plan.new:
                print("\nНовых доменов нет — все данные свежие. Прогон не нужен.")
                return EXIT_OK

            if not args.yes and not _confirm():
                print("Отменено.")
                return EXIT_CANCELLED

            settings = await runs.create_settings(
                defaults(),
                geo_top_n=filters.GEO_TOP_N,
                geo_min_share=filters.GEO_MIN_SHARE,
                metrics_ttl_days=filters.METRICS_TTL_DAYS,
                price_ttl_days=filters.PRICE_TTL_DAYS,
                units_cap=allowed,
            )
            deps = RunDeps(provider=provider, client=client, donors=donors, runs=runs)
            report = await execute_run(
                deps,
                RunRequest(
                    keywords=keywords,
                    country=args.country,
                    thresholds=defaults(),
                    settings_id=settings.id,
                    cap=allowed,
                    depth_pages=args.depth,
                    # Выдачу уже купили — по ней показана смета и получено
                    # подтверждение. Без этой строки прогон покупал бы её
                    # второй раз, и подтверждение стоило бы денег.
                    candidates=candidates,
                ),
            )
            await session.commit()
            _print_report(report)
    finally:
        await client.aclose()
        await engine.dispose()

    return EXIT_OK


def _confirm() -> bool:
    try:
        answer = input("\nЗапускать? [y/N] ").strip().lower()
    except EOFError:
        # Неинтерактивный запуск без --yes: молча потратить нельзя.
        print("\nНет терминала для подтверждения. Для автоматического запуска — --yes.")
        return False
    return answer in {"y", "yes", "д", "да"}


def _print_report(report: RunReport) -> None:
    by_status = report.by_status
    print("\n── Итог ──")
    for status, count in sorted(by_status.items(), key=lambda kv: kv[0].value):
        print(f"  {status.value:<12} {count}")
    reasons = report.reject_reasons
    if reasons:
        print("\nПричины отказа:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<24} {count}")
    spent = report.spent_units
    error = report.estimate_error
    print(f"\nПотрачено:           {spent:,} юнитов".replace(",", " "))
    for operation, units in sorted(report.spent_by_operation.items(), key=lambda kv: -kv[1]):
        print(f"  {operation:<18} {units:,}".replace(",", " "))
    if report.free_by_operation:
        total_free = sum(report.free_by_operation.values())
        print(f"\nБесплатно из кэша Ahrefs: {total_free} запросов")
        for operation, count in sorted(report.free_by_operation.items(), key=lambda kv: -kv[1]):
            print(f"  {operation:<18} {count}")
    if abs(error) > 0.2:
        assumed = 0.39  # доля, заложенная в смету по замеру Ф2
        actual = report.actual_pass_share
        print(f"\nСмета разошлась с фактом на {error:+.0%}.")
        if abs(actual - assumed) > 0.1:
            # Воронка объясняет расхождение чаще, чем цены: она зависит от
            # ниши, а цены у провайдера меняются редко.
            print(
                f"  Причина, скорее всего, в воронке: до запроса по странам дошли "
                f"{actual:.0%} проверенных вместо заложенных {assumed:.0%}. "
                f"Сильная выдача даёт сильные домены, и дорогая ступень видит больше."
            )
        else:
            print(
                "  Воронка совпала с ожидаемой — значит, изменились цены. "
                "Стоит перемерить: scripts/measure_units.py"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outreach", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("quota", help="остаток юнитов Ahrefs (бесплатно)")

    run = sub.add_parser("run", help="прогон: выдача, отбор доноров, сохранение")
    run.add_argument("--keywords", required=True, help="файл со списком ключей, по одному в строке")
    run.add_argument("--country", required=True, help="код страны ISO-2, например us")
    run.add_argument(
        "--depth", type=int, default=1, help="страниц выдачи по десять (по умолчанию 1)"
    )
    run.add_argument("--cap", type=int, help="потолок расхода на прогон, юнитов")
    run.add_argument("--yes", action="store_true", help="не спрашивать подтверждения")

    user_add = sub.add_parser("user-add", help="завести учётку и показать разовый пароль")
    user_add.add_argument("--email", required=True, help="почта сотрудника, она же логин")
    user_add.add_argument(
        "--role",
        default="operator",
        choices=["admin", "operator"],
        help="роль: admin заводит учётки, operator работает с базой",
    )

    user_reset = sub.add_parser("user-reset", help="выдать новый разовый пароль")
    user_reset.add_argument("--email", required=True, help="почта сотрудника")

    add_keywords_parser(sub)

    demo = sub.add_parser(
        "demo-seed",
        help="выдуманные домены рассылки и переписка — чтобы посмотреть экраны",
    )
    demo.add_argument(
        "--clear",
        action="store_true",
        help="убрать выдуманные данные (всё на .example.test), не трогая остальные",
    )

    contacts = sub.add_parser("contacts", help="поиск контактов подходящим донорам")
    contacts.add_argument(
        "--limit", type=int, default=100, help="сколько доноров взять за раз (по умолчанию 100)"
    )
    contacts.add_argument(
        "--no-paid",
        action="store_true",
        help="только бесплатные ступени: MX, страницы, RDAP",
    )
    contacts.add_argument(
        "--browser",
        action="store_true",
        help="ступень браузера для сайтов, которые не открылись обычным запросом",
    )
    contacts.add_argument(
        "--paid-first",
        action="store_true",
        help="сначала платный сервис, добор скрейпером: быстрее, но платных запросов больше",
    )

    add_letters_parser(sub)
    return parser


# Каждый вид отказа — свой код и своя формулировка. Вызывающий скрипт
# различает их кодом, человек — текстом; разбирать сообщение ради ветвления
# не приходится ни тому, ни другому.
_FAILURES: tuple[tuple[type[Exception], int, str], ...] = (
    (ConfigError, EXIT_MISCONFIGURED, "Не хватает настроек"),
    (UnknownProviderError, EXIT_MISCONFIGURED, "Источник выдачи не выбран"),
    (CapExceededError, EXIT_CAP_EXCEEDED, "Прогон не запущен"),
    (QuotaUnavailableError, EXIT_QUOTA_UNAVAILABLE, "Прогон не запущен"),
    (AhrefsError, EXIT_PROVIDER_FAILED, "Провайдер не ответил"),
)


#: Команда → что выполнить. Таблицей, а не цепочкой `if`: цепочка росла
#: с каждой новой командой и упёрлась в потолок сложности — а «добавить
#: команду» не то действие, ради которого стоит переписывать разбор.
_COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "quota": lambda _: cmd_quota(),
    "contacts": cmd_contacts,
    "user-add": cmd_user_add,
    "user-reset": cmd_user_reset,
    "keywords": cmd_keywords,
    "demo-seed": cmd_demo_seed,
    "letters-build": cmd_letters_build,
    "letters": cmd_letters,
    "letters-send": cmd_letters_send,
}


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    command = _COMMANDS.get(args.command, cmd_run)(args)

    try:
        return asyncio.run(command)
    except KeyboardInterrupt:
        print("\nПрервано. Уже сохранённые домены остались в базе.", file=sys.stderr)
        return EXIT_CANCELLED
    except Exception as exc:
        for kind, code, prefix in _FAILURES:
            if isinstance(exc, kind):
                print(f"{prefix}: {exc}", file=sys.stderr)
                return code
        # Не наш случай — пусть падает с трассировкой. Подменить её коротким
        # сообщением значит отнять единственную подсказку о причине.
        raise


if __name__ == "__main__":
    sys.exit(main())
