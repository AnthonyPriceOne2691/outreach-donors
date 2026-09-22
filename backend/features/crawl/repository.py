"""Сохранение обхода: запись прохода и найденные ссылки.

Сохраняется **всегда**, чем бы обход ни кончился. Запись обхода без
единой ссылки — это знание: она отличает «у донора нет исходящих
ссылок» от «нас не пустили» и от «robots запретил». Записав только
удачные проходы, мы получили бы базу, по которой закрытый донор
выглядит пустым.

Сырой HTML сюда не доезжает по устройству: обход отдаёт адреса, анкоры
и пометки, а страницы остаются в памяти прохода.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.walk import CrawlReport

logger = logging.getLogger(__name__)


async def save_crawl(session: AsyncSession, report: CrawlReport) -> CrawlRunModel:
    """Записать проход и его ссылки. Возвращает запись обхода.

    Ссылки добавляются к записи, а не отдельной операцией: у них нет
    смысла в отрыве от прохода, и разорвать их значит однажды получить
    ссылки без ответа на вопрос «а полным ли был обход, который их нашёл».
    """
    run = CrawlRunModel(
        host=report.host,
        outcome=report.outcome,
        stop_reason=report.stop_reason,
        pages_opened=len(report.pages),
        articles=report.articles,
        finished_at=datetime.now(UTC),
        stats=report.as_dict(),
        degradation=report.degradation or None,
    )
    run.links = [
        OutLinkModel(
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
        )
        for link in report.links
    ]

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
