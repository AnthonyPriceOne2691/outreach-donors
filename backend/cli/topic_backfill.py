"""Досуд тематики сайтов: `outreach topic-backfill`.

Главное свойство то же, что у прогона и досуда судьи: **план называется до траты.**
Команда показывает, скольким сайтам определит тему, откуда возьмёт текст, сколько это
токенов модели и денег по прайсу — и ждёт подтверждения. `--dry-run` — только план:
модель не зовётся, в базе ничего не меняется. Ahrefs и выдача не трогаются вовсе.

Прерванный досуд продолжается повтором той же команды: каждый ответ записан сразу вместе
со строкой журнала расхода, и сайты со свежей темой план уже не берёт. «Заново» (`--again`)
— тем, чья тема записана раньше момента старта; момент печатается в плане, и повтор после
обрыва с `--again-before <момент>` продолжает с места, а не платит второй раз.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import llm as llm_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.donors.home_signals import home_timeout
from backend.features.topics.backfill import (
    TopicBackfillReport,
    TopicPlan,
    backfill_topics,
    plan_topics,
)
from backend.features.topics.taxonomy import label_of
from backend.shared.net.url_guard import guarded_client
from backend.shared.numbers import spaced

EXIT_OK = 0
EXIT_DECLINED = 1

#: Откуда текст — словами (`classifier.site_text`, источник).
SOURCES = {
    "serp": "страница из выдачи прогона",
    "home": "заголовок главной из суда",
    "serp+home": "выдача прогона и заголовок главной",
}

#: Строка на прерывание (`cli/main._KEPT_ON_INTERRUPT`).
KEPT_ON_INTERRUPT = (
    "Каждый ответ модели записан сразу вместе со строкой расхода: определённое осталось в "
    "базе, потеряны только ответы, которые ещё летели. Повтор команды возьмёт остальных; "
    "после «--again» — с тем же --again-before, что напечатал план."
)


def _confirm() -> bool:
    try:
        answer = input("\nОпределить тему? [y/N] ").strip().lower()
    except EOFError:
        # Неинтерактивный запуск без --yes: молча потратить нельзя.
        print("\nНет терминала для подтверждения. Для автоматического запуска — --yes.")
        return False
    return answer in {"y", "yes", "д", "да"}


async def cmd_topic_backfill(args: argparse.Namespace) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            factory() as session,
            httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as http,
            guarded_client(timeout=home_timeout()) as home,
        ):
            return await run_topic_backfill(
                session, args, http=http, home_client=None if args.no_home else home
            )
    finally:
        await engine.dispose()


def again_before(args: argparse.Namespace) -> datetime | None:
    """Момент «заново»: названный (`--again-before`) или старт этого запуска (`--again`)."""
    if args.again_before is not None:
        moment: datetime = args.again_before
        return moment
    return datetime.now(UTC) if args.again else None


async def run_topic_backfill(
    session: AsyncSession,
    args: argparse.Namespace,
    *,
    http: httpx.AsyncClient,
    home_client: httpx.AsyncClient | None,
) -> int:
    """План, подтверждение, досуд, итог. Клиенты — снаружи: модель и главные."""
    plan = await plan_topics(
        session,
        limit=args.limit,
        again_before=again_before(args),
        fetch_home=not args.no_home,
    )
    print_plan(plan, no_home=args.no_home)
    if args.dry_run:
        print("\nЭто план: модель не звали, в базе ничего не изменилось.")
        return EXIT_OK
    if not plan.asked:
        return EXIT_OK
    if not args.yes and not _confirm():
        print("Не определяли.")
        return EXIT_DECLINED
    report = await backfill_topics(
        session,
        plan,
        http=http,
        home_client=home_client,
        progress=lambda done, total: print(f"  {done} из {total}", flush=True),
    )
    print_report(report)
    return EXIT_OK


def _print_again(plan: TopicPlan) -> None:
    if plan.again_before is None:
        return
    moment = plan.again_before.astimezone(UTC)
    print(
        f"  заново — тем, чья тема записана раньше {moment:%Y-%m-%d %H:%M:%S} UTC; "
        f"продолжить после обрыва: --again-before {moment.isoformat()}"
    )


def print_plan(plan: TopicPlan, *, no_home: bool) -> None:
    per = plan.per_site
    how = "по замеру прошлых прогонов" if per.measured else "по оценке, замера ещё не было"
    print(f"Определить тему: {spaced(len(plan.hosts))} сайтов (доноров: {spaced(plan.accepted)})")
    _print_again(plan)
    if plan.fresh:
        print(f"  уже с темой этой версии: {spaced(plan.fresh)} — не трогаем")
    if plan.skipped_reserved:
        print(f"  выдуманных (.test) пропущено: {plan.skipped_reserved}")
    for source, count in sorted(plan.by_source.items()):
        print(f"  текст есть ({SOURCES.get(source, source)}): {spaced(count)}")
    if plan.to_fetch:
        reserve = (
            f"; у {spaced(len(plan.fallback))} из них — заголовок из суда запасом, "
            "если главная не откроется"
            if plan.fallback
            else ""
        )
        print(
            "  откроем главную (бесплатно; `--no-home` — пропустить): "
            f"{spaced(len(plan.to_fetch))}{reserve}"
        )
    if plan.without_text:
        why = "главную не открываем (--no-home)" if no_home else "текста нет"
        print(f"  останутся без темы — {why}: {spaced(plan.without_text)}")
    print(
        f"Токены модели: до ≈ {spaced(plan.tokens)} — {spaced(per.tokens)} на сайт "
        f"(ввод {spaced(per.prompt)}, вывод {spaced(per.completion)}), {how}"
    )
    print(
        f"Деньги: до ≈ ${plan.usd:.2f} по прайсу модели, без скидки провайдера на повтор "
        "промпта. Токены идут в дневной потолок модели: выбран — досуд остановится, "
        "повтор команды продолжит"
    )


def print_report(report: TopicBackfillReport) -> None:
    summary = report.summary
    print("\n── Тематика ──")
    print(f"  тема определена: {spaced(summary.classified)}, не ясна: {spaced(summary.unclear)}")
    if summary.unreadable:
        print(
            f"  ⚠ ответ не по форме у {spaced(summary.unreadable)}: {summary.unreadable_reason}"
            " — темы у них нет, повтор команды возьмёт их снова"
        )
    if summary.unanswered:
        print(
            f"  ⚠ модель не ответила по {spaced(summary.unanswered)}: {summary.unanswered_reason}"
            " — темы у них нет, повтор команды возьмёт их снова"
        )
    if report.fetched or report.unreached:
        print(f"  главных открыто: {report.fetched}, не открылось: {report.unreached}")
    if report.by_title:
        print(f"  по заголовку из суда (главная не открылась): {report.by_title}")
    if report.no_text:
        print(f"  текста не нашлось: {report.no_text} — темы нет")
    for code, count in sorted(summary.by_topic.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {label_of(code):<16} {count}")
    print(f"  токенов {spaced(summary.tokens)}")
    print("\nРезультат — на «Донорах»: фильтр «Тематика».")


def _positive(text: str) -> int:
    """`--limit` — целое от единицы: ноль сайтов определять незачем."""
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"нужно целое число от 1, а не «{text}»")
    return int(text)


def _moment(text: str) -> datetime:
    """`--again-before` — момент ISO 8601, как его печатает план; без пояса — UTC."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"нужен момент ISO 8601, как его печатает план, а не «{text}»"
        ) from None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "topic-backfill", help="определить тематику сайтам, записанным до неё (токены модели)"
    )
    parser.add_argument(
        "--limit", type=_positive, default=None, help="сколько сайтов, сначала доноры"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="только план: сколько, токены и деньги; без траты"
    )
    parser.add_argument(
        "--no-home", action="store_true", help="не открывать главные: только сохранённый текст"
    )
    parser.add_argument(
        "--again",
        action="store_true",
        help="заново и тем, у кого тема свежая, — записанным раньше момента старта",
    )
    parser.add_argument(
        "--again-before",
        type=_moment,
        default=None,
        help="заново — записанным раньше этого момента (его печатает план): продолжить после обрыва",
    )
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждения")
