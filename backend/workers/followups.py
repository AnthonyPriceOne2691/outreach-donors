"""Рассылка добивок по расписанию: `python -m backend.workers.followups`.

Отдельный процесс, а не работа сервера: добивка уходит по сроку, а не
по нажатию, и ждать, пока кто-то откроет экран, нельзя. Отдельный
и от воркера очереди: у того задачи ставит человек, а здесь расписание,
и падение одного не должно останавливать другое.

Проход короткий и частый. Подошедших добивок к утру может оказаться
сотня, и отправить их подряд значит выдать всплеск, по которому
почтовая платформа судит о рассылке хуже, чем по объёму.

Второй цикл — уборка брошенных файлов ответа (`letters/outgoing_store.drop_abandoned`):
файл, приложенный к переписке и не ушедший ни с одним письмом за неделю. Здесь, а не в
reaper: файлы наших писем — предмет процесса писем по расписанию, а циклы reaper тесты
прохода передачи лидов продаж судят набором — новый цикл там ходил бы в базу и в них.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.letters.followups import send_due
from backend.features.letters.outgoing_store import OutgoingFiles
from backend.features.letters.transport_factory import Transports, in_use
from backend.shared.logs import setup_logging
from backend.workers.ticker import every

logger = logging.getLogger(__name__)

#: Как часто заглядывать, не пора ли кому добивку.
POLL_INTERVAL_SEC = 60.0

#: Сколько добивок за проход. Потолок часа на ящик стоит отдельно
#: (`OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR`); это ограничение прохода,
#: чтобы одна минута не держала процесс полчаса.
BATCH = 20

#: Как часто убирать брошенные файлы ответа. Сроку у них неделя
#: (`outgoing_files.PENDING_DAYS`): проход раз в шесть часов — с запасом и без лишних
#: запросов к базе. Первый — при старте: процесс перезапускается каждой выкаткой, и
#: проход «через шесть часов после старта» при частых выкатках не доходил бы до дела.
ABANDONED_FILES_SEC = 6 * 3600.0


async def sweep() -> None:
    """Один проход по подошедшим добивкам.

    Транспорт собирается на каждый проход вместе с сессией и закрывается
    с ней же: настройки отправки меняются без перезапуска процесса,
    а незакрытый транспорт оставлял бы по пулу соединений на каждый проход.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Добивки прохода — разных этапов: каждая уходит учёткой своего этапа.
        async with factory() as session, in_use(Transports()) as transports:
            report = await send_due(session, transport=transports, limit=BATCH)
        if report.sent or report.postponed or report.stopped:
            logger.info("Добивки: %s", report.as_report)
    finally:
        await engine.dispose()


async def drop_abandoned_files() -> None:
    """Один проход уборки брошенных файлов ответа. Строка журнала — только если что-то
    убрано: номера файлов и переписок полями — по ним ищут «куда делся файл»."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            gone = await OutgoingFiles(session).drop_abandoned(now=datetime.now(UTC))
            await session.commit()
    finally:
        await engine.dispose()
    if gone:
        logger.info(
            "файлы ответа: убраны брошенные — %s",
            len(gone),
            extra={
                "files": [file_id for file_id, _ in gone],
                "threads": sorted({t for _, t in gone}),
            },
        )


async def _loops() -> None:
    """Добивки и уборка файлов — в одном процессе, падение одного прохода не
    останавливает другой: этим занимается `every`."""
    await asyncio.gather(
        every(POLL_INTERVAL_SEC, sweep, name="Добивки"),
        every(ABANDONED_FILES_SEC, drop_abandoned_files, name="Чистка брошенных файлов ответа"),
    )


def main() -> None:
    setup_logging()
    check_storage()
    asyncio.run(_loops())


if __name__ == "__main__":
    main()
