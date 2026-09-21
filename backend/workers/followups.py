"""Рассылка добивок по расписанию: `python -m backend.workers.followups`.

Отдельный процесс, а не работа сервера: добивка уходит по сроку, а не
по нажатию, и ждать, пока кто-то откроет экран, нельзя. Отдельный
и от воркера очереди: у того задачи ставит человек, а здесь расписание,
и падение одного не должно останавливать другое.

Проход короткий и частый. Подошедших добивок к утру может оказаться
сотня, и отправить их подряд значит выдать всплеск, по которому
почтовая платформа судит о рассылке хуже, чем по объёму.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.letters.followups import send_due
from backend.features.letters.transport import build_transport
from backend.shared.logs import setup_logging
from backend.workers.ticker import every

logger = logging.getLogger(__name__)

#: Как часто заглядывать, не пора ли кому добивку.
POLL_INTERVAL_SEC = 60.0

#: Сколько добивок за проход. Потолок часа на ящик стоит отдельно
#: (`OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR`); это ограничение прохода,
#: чтобы одна минута не держала процесс полчаса.
BATCH = 20


async def sweep() -> None:
    """Один проход по подошедшим добивкам.

    Транспорт собирается на каждый проход вместе с сессией: настройки
    отправки меняются без перезапуска процесса, и долгоживущий транспорт
    продолжал бы слать по-старому.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await send_due(session, transport=build_transport(), limit=BATCH)
        if report.sent or report.postponed or report.stopped:
            logger.info("Добивки: %s", report.as_report)
    finally:
        await engine.dispose()


def main() -> None:
    setup_logging()
    check_storage()
    asyncio.run(every(POLL_INTERVAL_SEC, sweep, name="Добивки"))


if __name__ == "__main__":
    main()
