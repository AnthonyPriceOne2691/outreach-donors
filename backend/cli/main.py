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

from backend.cli import donor_add, geo_shares, geo_top5
from backend.cli.access_admin import cmd_user_add, cmd_user_reset
from backend.cli.advertisers import add_parser as add_advertisers_parser
from backend.cli.advertisers import (
    cmd_advertiser_decide,
    cmd_advertisers,
    cmd_advertisers_promote,
    cmd_suppliers_import,
)
from backend.cli.contact_refind import add_parser as add_contact_refind_parser
from backend.cli.contact_refind import cmd_contacts_refind
from backend.cli.contact_search import cmd_contacts
from backend.cli.contact_sweep import add_parser as add_contact_sweep_parser
from backend.cli.contact_sweep import cmd_contacts_file
from backend.cli.crawl_probe import add_parser as add_crawl_parser
from backend.cli.crawl_probe import cmd_crawl
from backend.cli.demo_data import cmd_demo_seed
from backend.cli.doors import add_parser as add_doors_parser
from backend.cli.doors import cmd_doors
from backend.cli.judge_backfill import add_parser as add_backfill_parser
from backend.cli.judge_backfill import cmd_judge_backfill
from backend.cli.keywords_pool import add_parser as add_keywords_parser
from backend.cli.keywords_pool import cmd_keywords
from backend.cli.letters_queue import add_parser as add_letters_parser
from backend.cli.letters_queue import cmd_letters, cmd_letters_build, cmd_letters_send
from backend.cli.mail_test import add_parser as add_mail_test_parser
from backend.cli.mail_test import cmd_mail_test
from backend.cli.probe_advertiser import add_parser as add_probe_advertiser_parser
from backend.cli.probe_advertiser import cmd_probe_advertiser
from backend.cli.probe_donor import add_parser as add_probe_donor_parser
from backend.cli.probe_donor import cmd_probe_donor
from backend.cli.prune import add_parser as add_prune_parser
from backend.cli.prune import cmd_prune
from backend.cli.review_queue import add_parser as add_review_queue_parser
from backend.cli.review_queue import cmd_review_queue
from backend.cli.run_judge import print_judge as _print_judge
from backend.cli.sales_commands import COMMANDS as SALES_COMMANDS
from backend.cli.sales_commands import FAILURES as SALES_FAILURES
from backend.cli.sales_commands import KEPT_ON_INTERRUPT as SALES_KEPT
from backend.cli.sales_commands import add_parsers as add_sales_parsers
from backend.cli.senders_admin import add_parser as add_senders_parser
from backend.cli.senders_admin import cmd_sender_add, cmd_senders
from backend.cli.sending_domains import add_parser as add_domains_parser
from backend.cli.sending_domains import cmd_sending_domain
from backend.config import storage
from backend.config.startup_checks import ConfigError, check_collect, check_storage
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.doors import door_check
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.donors.repository import DonorRepository
from backend.features.review.candidates import RunReview
from backend.features.runs.ahrefs_key import keeper
from backend.features.runs.budget import (
    CapExceededError,
    QuotaUnavailableError,
    cap_left,
    units_left,
)
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import RunPlan, gather_candidates, plan_run
from backend.features.runs.report import RunReport
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import rules_in_force
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


def _of(used: int | None, limit: int | None, *, missing: str) -> str:
    """«выбрано из лимита» — числами; лимит не назван — словами `missing`, а не нулём."""
    if limit is None:
        return missing
    return f"{used or 0:,} из {limit:,}".replace(",", " ")


async def cmd_quota() -> int:
    """Остаток юнитов. Запрос бесплатный. Пустой лимит — словами (16.10.2026): у ключа без
    своего лимита действует лимит пространства, и «0 из 0» читалось бы как выбранный ключ."""
    client = AhrefsClient()
    try:
        quota = Quota.from_payload(await client.limits_and_usage())
    finally:
        await client.aclose()

    own = "своего лимита нет — действует лимит пространства"
    print(f"Доступно юнитов:     {quota.available:,}".replace(",", " "))
    print(f"  ключ:              {_of(quota.key_used, quota.key_limit, missing=own)}")
    print(
        "  пространство:      "
        + _of(quota.workspace_used, quota.workspace_limit, missing="Ahrefs лимита не назвал")
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
    if p.excluded:
        print(f"\nИсключены:           {len(p.excluded)}  (в прогон не идут)")
        for reason, count in sorted(p.excluded_by_reason.items()):
            print(f"  {reason + ':':<20} {count}")
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
    if p.savings_from_gate:
        print(f"Сэкономлено гейтом:  {p.savings_from_gate:,} юнитов".replace(",", " "))


async def cmd_run(args: argparse.Namespace) -> int:
    """Прогон: смета, подтверждение, сбор."""
    check_storage()
    check_collect()

    keywords = _read_keywords(Path(args.keywords))
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client: AhrefsClient | None = None

    try:
        async with factory() as session:
            # Версия настроек — действующая, как у кнопки: пороги, правило региона
            # и срок метрик (сверка ТЗ 11.10.2026). Несогласованное правило региона —
            # отказ до первого запроса: фильтр иначе молча отсеивал бы годных (`geo.py`).
            rules = await rules_in_force(session)
            assert_settings_allow_limited_fetch(rules.geo.top_n, rules.geo.min_share)
            client = AhrefsClient()
            provider = build_provider(client)
            donors = DonorRepository(session)
            runs = RunRepository(session)

            candidates = await gather_candidates(
                provider, keywords, args.country, depth_pages=args.depth
            )
            # Остаток у Ahrefs — первым: его ответ ложится в снимок ключа, и кап ниже
            # считается от размера ключа на эту минуту (16.10.2026).
            provider_left = await units_left(client, keep=keeper(session))
            # Кап — на цикл ключа: из него вычитается уже потраченное нами, иначе он
            # ограничивает один прогон, а не цикл. Свой потолок (`--cap`) может быть
            # только меньше. Кап — тот же, что у кнопки: от ключа и с экрана «Расход».
            left = await cap_left(session)
            allowed = min(args.cap, left) if args.cap is not None else left
            budget = min(provider_left, allowed)
            plan = await plan_run(
                candidates,
                donors,
                units_left=budget,
                exclusions=Exclusions(session),
                country_share=await runs.country_call_share(args.country),
                metrics_ttl_days=rules.metrics_ttl_days,
            )
            _print_plan(plan, budget)

            if not plan.new:
                print("\nНовых доменов нет — все данные свежие. Прогон не нужен.")
                return EXIT_OK

            if not args.yes and not _confirm():
                print("Отменено.")
                return EXIT_CANCELLED

            # Пороги — действующие, с экрана «Пороги», как у кнопки (аудит 10.10.2026).
            settings = await runs.create_settings(
                rules.thresholds, **rules.columns(), units_cap=allowed
            )
            async with door_check() as doors:
                deps = RunDeps(
                    provider=provider,
                    client=client,
                    donors=donors,
                    runs=runs,
                    exclusions=Exclusions(session),
                    review=RunReview(session),
                    doors=doors,
                )
                report = await execute_run(
                    deps,
                    RunRequest(
                        keywords=keywords,
                        country=args.country,
                        thresholds=rules.thresholds,
                        geo=rules.geo,
                        metrics_ttl_days=rules.metrics_ttl_days,
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
        if client is not None:
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
    _print_judge(report)
    _print_review(report)
    if abs(error) > 0.2:
        print(f"\nСмета разошлась с фактом на {error:+.0%}.")
        # Смета — верхняя граница: метрики на все новые домены, запрос по странам —
        # худшей долей прошедших пороги в недавних прогонах страны
        # (okf/funnel-calibration.md). Перерасход значит, что прогон дороже своей истории.
        if error > 0:
            print(
                "  Трата выше сметы: пороги прошло больше доменов, чем в прошлых "
                "прогонах этой страны (каждому — запрос по странам), или изменились "
                "цены — scripts/measure_units.py"
            )


def _print_review(report: RunReport) -> None:
    """Прогон кончается очередью: сказать, сколько ждёт человека и где."""
    review = report.review
    if review is None:
        return
    print(f"\nНа рассмотрение:      {review.pending}")
    for decision, count in review.carried.items():
        print(f"  решено раньше ({decision}): {count}")
    if review.not_writing:
        print(f"  не положены — кому не пишем: {review.not_writing}")
    print("  Решить — экран прогона: контакты и письма получат только принятые.")


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
    add_backfill_parser(sub)
    add_review_queue_parser(sub)
    add_doors_parser(sub)

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
        help="только бесплатные ступени: MX, страницы (и RDAP, если включена)",
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

    add_contact_sweep_parser(sub)
    add_contact_refind_parser(sub)
    add_crawl_parser(sub)
    add_advertisers_parser(sub)
    add_letters_parser(sub)
    add_senders_parser(sub)
    add_domains_parser(sub)
    add_mail_test_parser(sub)
    add_prune_parser(sub)
    geo_shares.add_parser(sub)
    geo_top5.add_parser(sub)
    add_probe_donor_parser(sub)
    add_probe_advertiser_parser(sub)
    donor_add.add_parser(sub)
    add_sales_parsers(sub)
    return parser


# Каждый вид отказа — свой код и своя формулировка. Вызывающий скрипт
# различает их кодом, человек — текстом; разбирать сообщение ради ветвления
# не приходится ни тому, ни другому.
_FAILURES: tuple[tuple[type[Exception], int, str], ...] = (
    (ConfigError, EXIT_MISCONFIGURED, "Не хватает настроек"),
    (UnknownProviderError, EXIT_MISCONFIGURED, "Источник выдачи не выбран"),
    (CapExceededError, EXIT_CAP_EXCEEDED, "Прогон не запущен"),
    (LlmCapExceededError, EXIT_CAP_EXCEEDED, "Остановлено"),
    (QuotaUnavailableError, EXIT_QUOTA_UNAVAILABLE, "Прогон не запущен"),
    (AhrefsError, EXIT_PROVIDER_FAILED, "Провайдер не ответил"),
    *SALES_FAILURES,
)


#: Что осталось после прерывания, если команда не сказала сама. Прогон по
#: файлу базы не касается: его пройденное — в чекпойнте. Первый Ctrl-C он
#: разбирает сам (`contact_sweep`), сюда доходит второй — пока пишется итог.
_KEPT_ON_INTERRUPT: dict[str, str] = {
    "contacts-file": "Пройденное — в чекпойнте: повторный запуск продолжит с места обрыва "
    "и запишет итог.",
    "prune": "Чистка идёт одной транзакцией: в базе ничего не изменилось.",
    "geo-shares": "Пересчёт идёт одной транзакцией: в базе ничего не изменилось.",
    "geo-top5": "Каждый донор фиксируется сразу вместе со строкой расхода: докупленное "
    "осталось в базе, повторный запуск возьмёт остальных.",
    "probe-donor": "Липовый донор заводится одной транзакцией: в базе ничего не изменилось.",
    "probe-advertiser": "Пробный заводится одной транзакцией: в базе ничего не изменилось.",
    **SALES_KEPT,
}


#: Команда → что выполнить. Таблицей, а не цепочкой `if`: цепочка росла
#: с каждой новой командой и упёрлась в потолок сложности — а «добавить
#: команду» не то действие, ради которого стоит переписывать разбор.
_COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "quota": lambda _: cmd_quota(),
    "contacts": cmd_contacts,
    "contacts-file": cmd_contacts_file,
    "contacts-refind": cmd_contacts_refind,
    "crawl": cmd_crawl,
    "advertisers": cmd_advertisers,
    "advertiser-decide": cmd_advertiser_decide,
    "advertisers-promote": cmd_advertisers_promote,
    "suppliers-import": cmd_suppliers_import,
    "user-add": cmd_user_add,
    "user-reset": cmd_user_reset,
    "keywords": cmd_keywords,
    "judge-backfill": cmd_judge_backfill,
    "review-queue": cmd_review_queue,
    "doors": cmd_doors,
    "demo-seed": cmd_demo_seed,
    "letters-build": cmd_letters_build,
    "letters": cmd_letters,
    "letters-send": cmd_letters_send,
    "sender-add": cmd_sender_add,
    "senders": cmd_senders,
    "sending-domain": cmd_sending_domain,
    "mail-test": cmd_mail_test,
    "prune": cmd_prune,
    "geo-shares": geo_shares.cmd_geo_shares,
    "geo-top5": geo_top5.cmd_geo_top5,
    "probe-donor": cmd_probe_donor,
    "probe-advertiser": cmd_probe_advertiser,
    "donor-add": donor_add.cmd_donor_add,
    **SALES_COMMANDS,
}


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    command = _COMMANDS.get(args.command, cmd_run)(args)

    try:
        return asyncio.run(command)
    except KeyboardInterrupt:
        kept = _KEPT_ON_INTERRUPT.get(args.command, "Уже сохранённые домены остались в базе.")
        print(f"\nПрервано. {kept}", file=sys.stderr)
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
