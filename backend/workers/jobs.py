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

from backend.config import ahrefs as ahrefs_cfg
from backend.config import filters, storage
from backend.config.startup_checks import check_collect, check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteClient
from backend.features.replies.extract import ExtractClient
from backend.features.replies.pipeline import Parser
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from backend.features.serp.factory import build_provider
from backend.shared.logs import setup_logging


def _thresholds() -> Thresholds:
    return Thresholds(
        min_dr=filters.MIN_DR,
        min_org_traffic=filters.MIN_ORG_TRAFFIC,
        min_refdomains=filters.MIN_REFDOMAINS,
        min_keywords=filters.MIN_KEYWORDS,
    )


async def _run(
    keywords: Sequence[str], country: str, cap: int | None, depth_pages: int
) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AhrefsClient()
    provider = build_provider(client)
    try:
        async with factory() as session:
            runs = RunRepository(session)
            thresholds = _thresholds()
            settings = await runs.create_settings(
                thresholds,
                geo_top_n=filters.GEO_TOP_N,
                geo_min_share=filters.GEO_MIN_SHARE,
                metrics_ttl_days=filters.METRICS_TTL_DAYS,
                price_ttl_days=filters.PRICE_TTL_DAYS,
                units_cap=cap or ahrefs_cfg.UNITS_CAP,
            )
            report = await execute_run(
                RunDeps(
                    provider=provider,
                    client=client,
                    donors=DonorRepository(session),
                    runs=runs,
                ),
                RunRequest(
                    keywords=list(keywords),
                    country=country,
                    thresholds=thresholds,
                    settings_id=settings.id,
                    cap=cap or ahrefs_cfg.UNITS_CAP,
                    depth_pages=depth_pages,
                ),
            )
            await session.commit()
            return {
                "checked": len(report.plan.new),
                "by_status": {status.value: n for status, n in report.by_status.items()},
                "spent_units": report.spent_units,
                "estimated_units": report.plan.estimate.total,
            }
    finally:
        await client.aclose()
        await engine.dispose()


def run_donor_search(
    keywords: Sequence[str],
    country: str,
    *,
    cap: int | None = None,
    depth_pages: int = 1,
) -> dict[str, Any]:
    """Прогон от ключей до сохранённых доноров. Возвращает короткий итог:
    подробности всё равно лежат в базе, а в очереди им не место.

    Проверки конфига здесь те же, что у консольной команды, и это не
    дублирование: задача из очереди идёт мимо неё, а прогон тратит
    деньги. Без этой строки первая же проверка проводки в докере ушла
    в живой Ahrefs и стоила 670 юнитов.
    """
    setup_logging()
    check_storage()
    check_collect()
    return asyncio.run(_run(keywords, country, cap, depth_pages))


async def _build_letters(
    campaign: str, country: str, niche: Sequence[str], limit: int
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
    return asyncio.run(_build_letters(campaign, country, niche, limit))


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
