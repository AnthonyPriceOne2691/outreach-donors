"""Команда `crawl`: обойти донора и назвать числа, которых нет в тестах.

Это измерительный прибор, а не рабочий проход. Ради одного числа —
**доли страниц под антибот-защитой** — и написан весь срез: от него
зависит смета Этапа 2 с шестикратным разбросом ($250–400 при 5%,
$600–900 при 20%, больше полутора тысяч при 50%). Угадать его нельзя,
оно зависит от того, какие доноры попали в базу.

Прибор ходит по чужим живым сайтам, поэтому у него нет умолчания
«обойти всех»: домены называются руками, по одному. Своей записи
в базе он не заводит — отчёт печатается и, если попросили, ложится
файлом рядом с замером.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import crawl as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.walk import CrawlReport, DonorCrawler
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)

EXIT_NOTHING_CRAWLED = 4

#: Что означает каждый исход для человека, читающего отчёт.
MEANING: dict[CrawlOutcome, str] = {
    CrawlOutcome.OK: "обойдён",
    CrawlOutcome.PARTIAL: "обойдён частично — упёрлись в срок, попытки или здоровье",
    CrawlOutcome.FORBIDDEN: "robots.txt запрещает — донора смотрит человек",
    CrawlOutcome.BLOCKED: "закрылся от нас — нужен следующий уровень каскада",
    CrawlOutcome.FAILED: "не открылся вовсе — это поломка, а не защита",
}


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    crawl = sub.add_parser("crawl", help="обойти донора и замерить долю закрытых страниц")
    crawl.add_argument(
        "domains",
        nargs="+",
        help="домены доноров через пробел, без схемы: example.com",
    )
    crawl.add_argument(
        "--pages",
        type=int,
        default=cfg.MAX_PAGES_PER_DONOR,
        help=f"потолок открытых страниц на донора (по умолчанию {cfg.MAX_PAGES_PER_DONOR})",
    )
    crawl.add_argument(
        "--seconds",
        type=float,
        default=cfg.MAX_SECONDS_PER_DONOR,
        help=f"потолок времени на донора (по умолчанию {cfg.MAX_SECONDS_PER_DONOR:.0f} с)",
    )
    crawl.add_argument(
        "--browser",
        action="store_true",
        help="включить уровень браузера для страниц, закрывшихся от обычного запроса",
    )
    # Режим по умолчанию — браузерный (решение по итогам замера), поэтому
    # флаг включает **обратное**: назваться своим именем. Прежний
    # `--as-browser` оставлен и ничего не делает: команда с ним в истории
    # у человека, и молча менять её смысл хуже, чем принять обе формы.
    crawl.add_argument(
        "--identify",
        action="store_true",
        help=(
            "представляться своим именем вместо браузера: пускают реже "
            "(замер: один донор из пяти закрывается), но запрет, "
            "адресованный нам в robots.txt, начинает работать"
        ),
    )
    crawl.add_argument("--as-browser", action="store_true", help=argparse.SUPPRESS)
    crawl.add_argument(
        "--save",
        action="store_true",
        help="записать проходы и найденные ссылки в базу",
    )
    crawl.add_argument(
        "--json",
        type=Path,
        default=None,
        help="куда положить отчёты замера файлом",
    )


def _print_report(report: CrawlReport) -> None:
    health = report.health
    print(f"\n=== {report.host} — {MEANING[report.outcome]}")
    print(f"  остановка:      {report.stop_reason.value}")
    print(f"  robots.txt:     {report.robots_status.value}", end="")
    print(f", пауза {report.crawl_delay} с" if report.crawl_delay else "")
    print(f"  список страниц: {report.source}", end="")
    if report.source == "sitemap":
        print(f" (дочитан: {'да' if report.sitemap_complete else 'нет'})")
    else:
        print()
    print(
        f"  запросов:       {health.get('requests', 0)} "
        f"(открыто {health.get('opened', 0)}, нет страницы {health.get('missing', 0)}, "
        f"закрыто {health.get('blocked', 0)}, ошибок {health.get('errors', 0)})"
    )
    print(f"  доля закрытых:  {_percent(health.get('blocked_share', 0))}")
    print(f"  время:          {report.elapsed_sec:.1f} с")
    print(f"  статей из них:  {report.articles}")
    advertisers = {link.target_root for link in report.links}
    in_body = sum(1 for link in report.links if link.in_body)
    print(
        f"  внешних ссылок: {len(report.links)} на {len(advertisers)} доменов "
        f"(в теле статьи {in_body}, рядом {len(report.links) - in_body})"
    )
    if report.slowed_down:
        print("  темп снижался:  да — отказов было выше порога")
    if report.crawl_delay and report.stop_reason is StopReason.TIMEOUT:
        # Арифметика, которую иначе считает человек и обычно не считает:
        # пауза сайта умножается на число страниц и упирается в срок.
        print(
            f"  ВНИМАНИЕ: сайт просит паузу {report.crawl_delay:.0f} с — "
            f"это {report.crawl_delay:.0f} с на страницу. Больше страниц "
            f"здесь стоит только времени: --seconds побольше."
        )
    for level, reason in report.degradation.items():
        print(f"  НЕ СРАБОТАЛ уровень {level}: {reason}")


def _percent(share: float | int) -> str:
    return f"{share * 100:.1f}%"


def _print_summary(reports: list[CrawlReport], *, as_browser: bool) -> None:
    """Итог замера: то самое число, ради которого всё это писалось."""
    requests = sum(int(r.health.get("requests", 0)) for r in reports)
    blocked = sum(int(r.health.get("blocked", 0)) for r in reports)
    opened = sum(len(r.pages) for r in reports)

    print("\n" + "=" * 60)
    print(f"Режим:                {'браузером' if as_browser else 'своим именем'}")
    print(f"Доноров:              {len(reports)}")
    print(f"Страниц открыто:      {opened}")
    print(f"Из них статей:        {sum(r.articles for r in reports)}")
    print(f"Запросов сделано:     {requests}")
    if requests:
        print(f"Доля закрытых страниц: {_percent(blocked / requests)}")
        print(
            "\nЭто число — вход в смету Этапа 2: 5% дают $250–400 в месяц, "
            "20% — $600–900, 50% — больше полутора тысяч. "
            "Замер на одном доноре ничего не доказывает: нужны пять."
        )
    else:
        print("Ни одного запроса не сделано — смотреть исходы выше, а не это число.")

    links = [link for report in reports for link in report.links]
    if links:
        print("\n── Кому доноры ставят ссылки (из тела статей) ──")
        counts: dict[str, int] = {}
        follow: dict[str, int] = {}
        for link in links:
            counts[link.target_root] = counts.get(link.target_root, 0) + 1
            follow[link.target_root] = follow.get(link.target_root, 0) + int(link.dofollow)
        body: dict[str, int] = {}
        for link in links:
            body[link.target_root] = body.get(link.target_root, 0) + int(link.in_body)
        for root, number in sorted(counts.items(), key=lambda item: -item[1])[:15]:
            print(f"  {number:>3} ссылок  {root}  (dofollow {follow[root]}, в теле {body[root]})")
        guessed = sum(1 for link in links if link.root_guessed)
        if guessed:
            print(f"  из них с угаданным корнем домена: {guessed} — суффикс неизвестен снимку")

    for outcome in CrawlOutcome:
        hosts = [r.host for r in reports if r.outcome is outcome]
        if hosts:
            print(f"\n{MEANING[outcome]}: {', '.join(hosts)}")


async def _crawl_one(
    host: str, args: argparse.Namespace, renderer: object | None, *, identify: bool
) -> CrawlReport:
    """Один донор — один клиент и один ограничитель: замер идёт подряд,
    и делить между донорами тут нечего."""
    limiter = DomainLimiter()
    async with guarded_client(timeout=cfg.PAGE_TIMEOUT_SEC) as client:
        crawler = DonorCrawler(
            client,
            limiter=limiter,
            renderer=renderer,  # type: ignore[arg-type]
            use_browser=renderer is not None,
            identify=identify,
            max_pages=args.pages,
            max_seconds=args.seconds,
        )
        try:
            return await crawler.crawl(host)
        finally:
            await crawler.aclose()


async def _save(reports: list[CrawlReport]) -> None:
    """Положить проходы в базу. Сохраняются все, включая пустые: обход,
    который не нашёл ничего, — это знание, а не отсутствие строки."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for report in reports:
                await save_crawl(session, report)
            await session.commit()
        print(f"\nЗаписано проходов: {len(reports)}")
    finally:
        await engine.dispose()


async def cmd_crawl(args: argparse.Namespace) -> int:
    """Обойти названные домены и напечатать замер."""
    # Флаг командной строки сильнее настройки: прибор запускают руками
    # и ровно тогда, когда готовы заплатить секундами за закрытые сайты.
    use_browser = args.browser or cfg.BROWSER_ENABLED
    identify = args.identify or (cfg.IDENTIFY and not args.as_browser)

    reports: list[CrawlReport] = []
    async with contextlib.AsyncExitStack() as stack:
        # Браузер поднимается только когда его попросили: без этого условия
        # каждый запуск прибора искал бы необязательный пакет и предупреждал
        # о его отсутствии — то есть приучал не читать предупреждения.
        renderer = await stack.enter_async_context(PlaywrightRenderer()) if use_browser else None
        if use_browser and renderer is None:
            print("Браузер не поднялся — обход пойдёт без него, это будет видно в отчёте.")
        for host in args.domains:
            print(f"\nОбход {host}…")
            report = await _crawl_one(host, args, renderer, identify=identify)
            reports.append(report)
            _print_report(report)

    _print_summary(reports, as_browser=not identify)

    if args.save:
        await _save(reports)

    if args.json:
        args.json.write_text(
            json.dumps([r.as_dict() for r in reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nОтчёты замера: {args.json}")

    # Ни одной страницы — это не успех с нулём. Вызывающий скрипт должен
    # отличать «обошли и ничего не нашли» от «нас не пустили».
    if not any(r.pages for r in reports):
        return EXIT_NOTHING_CRAWLED
    return 0
