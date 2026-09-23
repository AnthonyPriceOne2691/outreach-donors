"""Задачи, которые выполняет воркер.

Задача — это тонкая обёртка над тем же ядром, что зовёт консольная
команда. Своей логики здесь нет намеренно: правило, появившееся в задаче,
не проверяется ни тестами ядра, ни тестами веба — оно живёт в третьем
месте, про которое вспоминают последним.

Асинхронный код запускается своим циклом событий: очередь синхронная,
и соединения базы обязаны создаваться в том же цикле, в котором
работают, — иначе первый же запрос падает на «attached to a different
loop».
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_collect, check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.contacts.search import search_contacts
from backend.features.donors.repository import DonorRepository
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteClient
from backend.features.replies.extract import ExtractClient
from backend.features.replies.pipeline import Parser
from backend.features.review.candidates import RunReview
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.lifecycle import heartbeat
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.features.serp.factory import build_provider
from backend.shared.logs import setup_logging


async def _run(run_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AhrefsClient()
    provider = build_provider(client)
    try:
        async with factory() as session:
            runs = RunRepository(session)
            run = await runs.get(run_id)
            # Удары о жизни идут своей короткой сессией: длинная в это
            # время занята пачкой доменов, и ждать её значит молчать
            # ровно тогда, когда прогон работает.
            async with factory() as ticker:
                beat = asyncio.create_task(heartbeat(RunRepository(ticker), run_id))
                try:
                    report = await execute_run(
                        RunDeps(
                            provider=provider,
                            client=client,
                            donors=DonorRepository(session),
                            runs=runs,
                            exclusions=Exclusions(session),
                            review=RunReview(session),
                        ),
                        RunRequest(
                            keywords=list(run.keywords),
                            country=run.country,
                            thresholds=defaults(),
                            settings_id=run.settings_id,
                            cap=run.settings.units_cap,
                            depth_pages=run.depth_pages,
                            run=run,
                        ),
                    )
                finally:
                    beat.cancel()
            await session.commit()
            return {
                "run": run_id,
                "checked": len(report.plan.new),
                "by_status": {status.value: n for status, n in report.by_status.items()},
                "spent_units": report.spent_units,
                "estimated_units": report.plan.estimate.total,
            }
    finally:
        await client.aclose()
        await engine.dispose()


def run_donor_search(run_id: int) -> dict[str, Any]:
    """Прогон от ключей до сохранённых доноров. Возвращает короткий итог:
    подробности всё равно лежат в базе, а в очереди им не место.

    Довод один — номер прогона. Ключи, страна, глубина и кап лежат в его
    строке: продолжение после смерти воркера ставит ту же задачу, и
    разъехаться её доводам не с чем.

    Проверки конфига здесь те же, что у консольной команды, и это не
    дублирование: задача из очереди идёт мимо неё, а прогон тратит
    деньги. Без этой строки первая же проверка проводки в докере ушла
    в живой Ahrefs и стоила 670 юнитов.
    """
    setup_logging()
    check_storage()
    check_collect()
    return asyncio.run(_run(run_id))


async def _build_letters(
    campaign: str,
    country: str,
    niche: Sequence[str],
    limit: int,
    followup_days: Sequence[int],
    letter_template: str | None,
    run_ids: Sequence[int],
) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rewriter = RewriteClient()
    try:
        async with factory() as session:
            report = await QueueBuilder(session, rewriter).build(
                BuildRequest(
                    campaign_name=campaign,
                    country=country,
                    niche=tuple(niche),
                    limit=limit,
                    followup_days=tuple(followup_days),
                    letter_template=letter_template,
                    run_ids=tuple(run_ids),
                )
            )
            await session.commit()
            return {
                "prepared": report.prepared,
                "tokens": report.tokens_spent,
                "off_corridor": report.off_corridor,
                "funnel": report.funnel,
                "blocked_by": report.blocked_by,
                "notes": report.notes,
                "bad_addresses": report.bad_addresses,
            }
    finally:
        await rewriter.aclose()
        await engine.dispose()


def build_letter_queue(
    campaign: str,
    country: str = "us",
    *,
    niche: Sequence[str] = (),
    limit: int = 50,
    followup_days: Sequence[int] = (),
    letter_template: str | None = None,
    run_ids: Sequence[int] = (),
) -> dict[str, Any]:
    """Собрать очередь писем. Ничего не отправляет.

    В очередь задач вынесено потому же, почему и прогон: каждое письмо
    стоит вызова модели, полсотни писем идут минутами, и выполнять это
    внутри запроса значит потерять работу, если человек закрыл вкладку.

    Проверки конфига здесь свои — задача из очереди идёт мимо тех, что
    стоят на маршруте.
    """
    setup_logging()
    check_storage()
    return asyncio.run(
        _build_letters(campaign, country, niche, limit, followup_days, letter_template, run_ids)
    )


async def _search_contacts(limit: int, use_browser: bool, paid_first: bool) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await search_contacts(
                session, limit=limit, use_browser=use_browser, paid_first=paid_first
            )
            await session.commit()
            return report.as_dict()
    finally:
        await engine.dispose()


def find_contacts(
    limit: int = 100, use_browser: bool = False, paid_first: bool = False
) -> dict[str, Any]:
    """Лестница контактов по донорам, которым он нужен.

    Задача, а не запрос: сотня доменов идёт минутами, и держать
    соединение всё это время значит потерять работу, если человек
    закрыл вкладку. Отчёт остаётся в результате задачи — по нему
    экран показывает, чем кончилось.
    """
    setup_logging()
    check_storage()
    return asyncio.run(_search_contacts(limit, use_browser, paid_first))


async def _parse_reply(reply_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    extractor = ExtractClient()
    try:
        async with factory() as session:
            parsed = await Parser(session, extractor).parse(reply_id)
            await session.commit()
            return {
                "reply": parsed.reply_id,
                "confidence": parsed.confidence,
                "stored_price": parsed.stored_price,
                "needs_review": parsed.needs_review,
                "tokens": parsed.tokens_spent,
            }
    finally:
        await extractor.aclose()
        await engine.dispose()


def parse_reply(reply_id: int) -> dict[str, Any]:
    """Разобрать один ответ в цену.

    Отдельной задачей, а не внутри вебхука: вызов модели идёт секундами,
    а платформа повторяет доставку по таймауту — платный разбор в запросе
    означал бы повторные списания там, где сеть подтормозила.
    """
    setup_logging()
    check_storage()
    return asyncio.run(_parse_reply(reply_id))
