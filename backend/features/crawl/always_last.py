"""Последняя часть цепочки донора в обходе 24/7: дала ли она новое — и ждать ли донору (`always.py`).

**Не пустил** (`LastPart.refused`): часть не открыла ни одной новой страницы, сайт закрылся или стал
отказывать — донор ждёт, пока его снова обойдут иначе (ночью или человек) и откроют страницы. Иначе
закрытый сайт ставился бы каждую минуту. Часть, остановленную разбором мёртвых, — так же.

**Впустую** (`LastPart.idle`): часть кончилась, цепочка не выросла ни на страницу (`CHAIN_TO`), а
потолок следующей тот же — донор ждёт так же. Следующая открыла бы те же адреса: 17–18.11.2026
адреса карты сайта, уводившие на уже открытые страницы, открывались каждой частью, и у одного донора
за ночь набралось 283 части по минуте. Потолок вырос (первые 10 000 пройдены) — не впустую:
следующей части есть куда идти.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.always_part import CHAIN_FROM, CHAIN_TO


@dataclass(frozen=True, slots=True)
class LastPart:
    """Последняя часть цепочки донора — то, что о ней нужно выдаче."""

    id: int
    number: int
    status: CrawlStatus
    outcome: CrawlOutcome | None
    stop: StopReason | None
    pages: int
    chain_from: int
    resumes: int
    #: Разных страниц у цепочки после части; нет — часть не кончилась или кончилась до 18.11.2026.
    chain_to: int | None = None
    #: Потолок части — страниц всей цепочки; нет — обычный потолок первой части.
    max_pages: int | None = None

    @property
    def refused(self) -> bool:
        """Часть не дала нового (шапка, «Не пустил»). Ночь и пауза — не отказ, а очередь на
        продолжение; отказ очереди задач (остановлен без единой задачи) — тоже не отказ сайта."""
        if self.status is CrawlStatus.STOPPED:
            return self.resumes > 0
        if self.status is not CrawlStatus.DONE or self.stop is StopReason.PAUSED:
            return False
        if self.outcome not in (CrawlOutcome.OK, CrawlOutcome.PARTIAL):
            return True
        return self.stop is StopReason.UNHEALTHY or self.pages <= self.chain_from

    def idle(self, ceiling: int | None) -> bool:
        """Часть кончилась, а цепочка не выросла ни на страницу, и у следующей потолок `ceiling`
        не выше — она открыла бы те же адреса (шапка, «Впустую»). Ночь и пауза — не конец."""
        if self.chain_to is None or self.status is not CrawlStatus.DONE:
            return False
        if self.stop is StopReason.PAUSED or self.chain_to > self.chain_from:
            return False
        return ceiling is None or self.max_pages is None or ceiling <= self.max_pages


async def last_parts(session: AsyncSession) -> dict[str, LastPart]:
    """Последняя часть цепочки каждого донора — любая: идущая, законченная, остановленная."""
    rows = await session.execute(
        select(
            CrawlRunModel.host,
            CrawlRunModel.id,
            func.coalesce(CrawlRunModel.depth_part, 0),
            CrawlRunModel.status,
            CrawlRunModel.outcome,
            CrawlRunModel.stop_reason,
            CrawlRunModel.pages_opened,
            func.coalesce(CrawlRunModel.stats[CHAIN_FROM].as_integer(), 0),
            CrawlRunModel.resumes,
            CrawlRunModel.stats[CHAIN_TO].as_integer(),
            CrawlRunModel.max_pages,
        )
        .where(CrawlRunModel.depth_part.is_not(None))
        .distinct(CrawlRunModel.host)
        .order_by(CrawlRunModel.host, CrawlRunModel.id.desc())
    )
    return {row[0]: LastPart(*row[1:]) for row in rows.all()}


def rests(last: LastPart | None, ceiling: int | None, regular: int) -> tuple[bool, bool]:
    """Повтор ли следующая часть и ждёт ли донор: последняя часть не пустила или прошла впустую, а
    обычным обходом (номер `regular`) сайт с тех пор не открывали."""
    if last is None:
        return False, False
    retry = last.refused or last.idle(ceiling)
    return retry, retry and regular <= last.id
