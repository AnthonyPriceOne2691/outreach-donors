"""Постановка обходов в очередь: кнопка «Запустить Этап 2» и её консольная пара.

Порядок один на экран и консоль — иначе у кнопки и команды разойдутся правила,
и разойдутся они молча:

1. строка обхода заводится до задачи — обход виден сразу, «в очереди»;
2. второй незаконченный обход донора не заводится — замок держит база
   (`repository.queue_crawl`), донор попадает в «уже идёт»;
3. номер задачи записывается в строку и фиксируется **до** постановки:
   обходчик, взявший задачу мгновенно, не должен застать пустую строку;
4. очередь не ответила — обход закрывается «остановлен» с причиной, и замок
   отпускает донора: оставить его «в очереди» без задачи значило бы ждать
   разбора мёртвых и тратить на это попытку.

Кого обходить, решает вызывающий: экран — по выбору человека из доноров со
свежей ценой (`targets.choose`), консоль — тем же отбором или списком руками.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlStatus
from backend.features.crawl.lifecycle import REASON_KEY, noted
from backend.features.crawl.repository import CrawlBusyError, queue_crawl

logger = logging.getLogger(__name__)

#: Поставить обход в очередь под заранее выданным номером задачи.
Enqueue = Callable[[int, str], object]

#: Выдать номер задачи обходу.
JobId = Callable[[int], str]


@dataclass(slots=True)
class Launch:
    """Что поставлено и что нет — с номерами, чтобы экран показал ход."""

    queued: dict[str, int] = field(default_factory=dict)
    """Донор → номер обхода."""
    busy: list[str] = field(default_factory=list)
    """Обход уже идёт или ждёт — второй не заведён."""
    failed: list[str] = field(default_factory=list)
    """Очередь не ответила — обход закрыт с причиной."""


async def launch(
    session: AsyncSession,
    hosts: Iterable[str],
    *,
    by: str | None,
    job_id: JobId,
    enqueue: Enqueue,
) -> Launch:
    """Поставить обходы доноров. Каждый донор фиксируется отдельно: отказ
    очереди на третьем не должен откатывать первые два."""
    done = Launch()
    for host in dict.fromkeys(h.strip().lower().removeprefix("www.") for h in hosts if h.strip()):
        try:
            run = await queue_crawl(session, host, by=by)
        except CrawlBusyError:
            logger.info("обход %s уже идёт или ждёт — второй не ставлю", host)
            done.busy.append(host)
            continue
        run.job_id = job_id(run.id)
        await session.commit()
        try:
            enqueue(run.id, run.job_id)
        except Exception as exc:
            logger.exception("обход %s (№%s): очередь не ответила", host, run.id)
            run.status = CrawlStatus.STOPPED
            run.stats = noted(run, **{REASON_KEY: f"не поставлен: очередь не ответила ({exc})"})
            await session.commit()
            done.failed.append(host)
            continue
        done.queued[host] = run.id
    logger.info(
        "обходы: поставлено %s, уже идут %s, очередь отказала %s",
        len(done.queued),
        len(done.busy),
        len(done.failed),
    )
    return done
