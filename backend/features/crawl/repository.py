"""Сохранение обхода: запись прохода и найденные ссылки.

Сохраняется **всегда**, чем бы обход ни кончился. Запись обхода без
единой ссылки — это знание: она отличает «у донора нет исходящих
ссылок» от «нас не пустили» и от «robots запретил». Записав только
удачные проходы, мы получили бы базу, по которой закрытый донор
выглядит пустым.

Путей записи два. Консольный замер пишет проход целиком в конце
(`save_crawl`). Обход задачей очереди идёт до получаса, поэтому его
строка заводится постановкой (`queue_crawl`), ссылки ложатся пачками
по ходу (`save_batch`), а исход — в конце (`finish_crawl`).

Сырой HTML сюда не доезжает по устройству: обход отдаёт адреса, анкоры
и пометки, а страницы остаются в памяти прохода.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.links import OutLink
from backend.features.crawl.progress import Batch
from backend.features.crawl.report import CrawlReport

logger = logging.getLogger(__name__)


class CrawlBusyError(ValueError):
    """У донора уже есть незаконченный обход — второй не заводится."""


def _row(link: OutLink) -> OutLinkModel:
    return OutLinkModel(
        page_url=link.page_url,
        url=link.url,
        target_host=link.target_host,
        target_root=link.target_root,
        anchor=link.anchor,
        anchor_key=link.anchor_key,
        nofollow=link.nofollow,
        sponsored=link.sponsored,
        ugc=link.ugc,
        in_body=link.in_body,
        root_guessed=link.root_guessed,
        page_label=link.page_label,
        page_published=link.page_published,
    )


async def save_crawl(session: AsyncSession, report: CrawlReport) -> CrawlRunModel:
    """Записать проход и его ссылки. Возвращает запись обхода.

    Ссылки добавляются к записи, а не отдельной операцией: у них нет
    смысла в отрыве от прохода, и разорвать их значит однажды получить
    ссылки без ответа на вопрос «а полным ли был обход, который их нашёл».
    """
    run = CrawlRunModel(
        host=report.host,
        status=CrawlStatus.DONE,
        outcome=report.outcome,
        stop_reason=report.stop_reason,
        pages_opened=len(report.pages),
        articles=report.articles,
        finished_at=datetime.now(UTC),
        stats=report.as_dict(),
        degradation=report.degradation or None,
    )
    run.links = [_row(link) for link in report.links]

    session.add(run)
    await session.flush()
    logger.info(
        "обход %s сохранён: исход %s, страниц %s, ссылок %s",
        report.host,
        report.outcome.value,
        len(report.pages),
        len(report.links),
    )
    return run


async def queue_crawl(session: AsyncSession, host: str, *, by: str | None) -> CrawlRunModel:
    """Завести обход в очереди — строкой до задачи, как прогон Этапа 1.

    Замок «один обход донора за раз» держит база (уникальный индекс по хосту
    среди незаконченных): две кнопки, нажатые разом, иначе завели бы два
    обхода одного сайта — двойная нагрузка на чужую машину и двойные ссылки.
    """
    run = CrawlRunModel(
        host=host.lower().removeprefix("www."), status=CrawlStatus.QUEUED, requested_by=by
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError as exc:
        raise CrawlBusyError(f"{run.host}: обход уже идёт или ждёт в очереди") from exc
    return run


async def save_batch(session: AsyncSession, run_id: int, batch: Batch) -> None:
    """Пачка ссылок и чекпоинт — вместе: записанное и «с чего продолжить»
    не расходятся (`crawl/progress.py`). Фиксирует вызывающий.

    Пачка заодно говорит «идёт»: разбор мёртвых ставит продолжению «в очереди»
    и может зафиксировать это уже после того, как новая задача взяла обход."""
    for link in batch.links:
        row = _row(link)
        row.crawl_run_id = run_id
        session.add(row)
    point = batch.checkpoint
    await session.execute(
        update(CrawlRunModel)
        .where(CrawlRunModel.id == run_id)
        .values(
            status=CrawlStatus.RUNNING,
            checkpoint=point.as_dict(),
            pages_opened=len(point.pages),
            articles=point.articles,
            updated_at=func.now(),
        )
    )
    await session.flush()


async def link_totals(session: AsyncSession, run_id: int) -> dict[str, int]:
    """Числа ссылок обхода — по базе. После продолжения у задачи в памяти
    только её часть ссылок, и отчёт посчитал бы обход меньше, чем он был."""
    totals = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(OutLinkModel.target_root)),
                func.coalesce(func.sum(OutLinkModel.in_body.cast(Integer)), 0),
                func.coalesce(func.sum(OutLinkModel.root_guessed.cast(Integer)), 0),
            ).where(OutLinkModel.crawl_run_id == run_id)
        )
    ).one()
    keys = ("links_found", "advertisers", "links_in_body", "roots_guessed")
    return {key: int(value) for key, value in zip(keys, totals, strict=True)}


async def finish_crawl(
    session: AsyncSession, run: CrawlRunModel, report: CrawlReport
) -> dict[str, Any]:
    """Закрыть обход исходом. Чекпоинт больше не нужен — стирается."""
    stats = {**report.as_dict(), **await link_totals(session, run.id), "resumes": run.resumes}
    run.status = CrawlStatus.DONE
    run.outcome = report.outcome
    run.stop_reason = report.stop_reason
    run.pages_opened = len(report.pages)
    run.articles = report.articles
    run.finished_at = datetime.now(UTC)
    run.stats = stats
    run.degradation = report.degradation or None
    run.checkpoint = None
    await session.flush()
    logger.info(
        "обход %s (%s) закончен: исход %s, страниц %s, ссылок %s",
        run.host,
        run.id,
        report.outcome.value,
        len(report.pages),
        stats["links_found"],
    )
    return stats
