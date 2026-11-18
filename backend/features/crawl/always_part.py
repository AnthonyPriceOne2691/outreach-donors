"""Часть обхода 24/7 в задаче обходчика: откуда начать, когда кончить и что оставить цепочке.

Часть ставит обход 24/7 (`always.py`) обычной строкой обхода с номером части (`depth_part`), и
ведёт её та же задача, что любой обход (`workers/crawl_jobs.py`). Отличий три.

**Начало — с чекпоинта цепочки** (`prepare`): страницы, открытые прежними частями, второй раз не
открываются, очередь ссылок идёт дальше, карта сайта читается заново. Потолок страниц строки — всей
цепочки: часть «до 20 000» у донора с 10 000 открытыми откроет ещё 10 000. Срок — на часть
(`always_depth.seed`). Продолжение после выкатки или смерти обходчика собирает чекпоинт цепочки и
записанное частью обратно — как у любого обхода.

**Конец — ещё и ночью и паузой** (`Part.paused`, `Progress.pause`): в окно ночного обхода (по часам,
как у `night.py`) и когда выключатель на паузе (база — не чаще раза в `SWITCH_EVERY_SEC`, `watch`),
часть кончается на границе страницы штатно: исход «частично», причина `paused`. Продолжения она себе
не ставит — следующую часть начнёт обход 24/7, когда ночь кончится или пуск нажмут снова.

**После конца — цепочке** (`finish`): открытое, очередь и сколько частей — в строку донора
(`always_depth.finish_part`), со скольких страниц цепочки часть начиналась и сколько у цепочки стало
после неё — в отчёт строки обхода (`CHAIN_FROM`, `CHAIN_TO`): по ним обход 24/7 видит, дала ли часть
новое. Счётчик открытого строки (`pages_opened`) этого не скажет: адрес, который уводит на уже
открытую страницу, он считает, а цепочка — нет.

**Карта сайта растёт с целью части** (`ceilings`): у глубокого обхода — 50 000 адресов на 10 000
страниц, у части «до 30 000» — 150 000. Не больше `MAP_SCALE_MAX` раз: на цепочке длиннее ста тысяч
страниц карту каждой части читали бы полчаса, а дальше её адресов обход идёт по ссылкам.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as cfg
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl import always_depth, always_switch
from backend.features.crawl.deep import Ceilings, ceilings_of
from backend.features.crawl.night import MOSCOW
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.report import CrawlReport

logger = logging.getLogger(__name__)

#: Как часто идущая часть спрашивает выключатель, с.
SWITCH_EVERY_SEC = 60.0
#: Во сколько раз больше глубокого обхода карта сайта читается самое большее (шапка).
MAP_SCALE_MAX = 10
#: Ключ в отчёте строки части: со скольких страниц цепочки она начиналась.
CHAIN_FROM = "chain_from"
#: Ключ в отчёте строки части: сколько разных страниц у цепочки после неё.
CHAIN_TO = "chain_to"


def night_hours(moment: datetime) -> bool:
    """Окно ночного обхода по часам: с полуночи по Москве на `CRAWL_NIGHT_WINDOW_SEC`, если ночной
    обход включён. Выключен — ночи нет, и обход 24/7 идёт круглые сутки."""
    if not crawl_cfg.NIGHT_ROUND_ENABLED:
        return False
    local = moment.astimezone(MOSCOW)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (local - midnight).total_seconds() < crawl_cfg.NIGHT_WINDOW_SEC


@dataclass(slots=True)
class Part:
    """Часть цепочки, которую ведёт задача."""

    number: int
    #: Чекпоинт цепочки на начало части; пусто — цепочка ещё ничего не открыла.
    base: Checkpoint | None
    #: Выключатель на паузе — по последнему опросу.
    off: bool = False

    @property
    def skip(self) -> int:
        """Страниц прежних частей: их нет в чекпоинте строки части (`always_depth.stored`)."""
        return 0 if self.base is None else len(self.base.pages)

    def stored(self, point: Checkpoint) -> dict[str, Any]:
        return always_depth.stored(point, self.skip)

    def paused(self) -> bool:
        """Пора кончать часть: пауза, рубильник сервиса или ночь."""
        return self.off or not cfg.ENABLED or night_hours(datetime.now(UTC))


def ceilings(max_pages: int | None) -> Ceilings:
    """Потолки части: как у обычного или глубокого обхода, а карта сайта — по цели (шапка)."""
    found = ceilings_of(max_pages)
    if max_pages is None or max_pages <= crawl_cfg.MAX_PAGES_DEEP:
        return found
    scale = min(max_pages / crawl_cfg.MAX_PAGES_DEEP, MAP_SCALE_MAX)
    return replace(
        found,
        sitemap_files=math.ceil(found.sitemap_files * scale),
        sitemap_urls=math.ceil(found.sitemap_urls * scale),
    )


async def prepare(
    session: AsyncSession, run: CrawlRunModel
) -> tuple[Checkpoint | None, Ceilings, Part]:
    """С чего идёт часть, под какими потолками и что о ней помнить до конца задачи."""
    row = await always_depth.row_of(session, run.host)
    base = always_depth.unpack(row.checkpoint if row is not None else None)
    part = Part(number=run.depth_part or 0, base=base)
    if run.checkpoint:
        point = always_depth.merged(base, run.checkpoint)
    else:
        point = always_depth.seed(base) if base is not None else None
    part.off = not (await always_switch.state(session)).enabled
    logger.info(
        "обход 24/7 %s: часть %s — %s страниц цепочки, %s",
        run.host,
        part.number,
        part.skip,
        "продолжение" if run.checkpoint else "начало",
    )
    return point, ceilings(run.max_pages), part


async def watch(
    factory: async_sessionmaker[AsyncSession], part: Part, *, every: float = SWITCH_EVERY_SEC
) -> None:
    """Спрашивать выключатель, пока задачу не отменят. Своя сессия на опрос, сбой — в журнал:
    часть идёт, как шла, до следующего опроса."""
    while True:
        await asyncio.sleep(every)
        try:
            async with factory() as session:
                part.off = not (await always_switch.state(session)).enabled
        except Exception:
            logger.exception("обход 24/7, часть %s: выключатель не прочитан", part.number)


async def finish(
    session: AsyncSession,
    run: CrawlRunModel,
    part: Part,
    report: CrawlReport,
    own: Mapping[str, Any] | None,
) -> None:
    """Часть кончилась: со скольких страниц цепочки она шла и сколько их стало — в отчёт строки,
    остальное — цепочке. `own` — чекпоинт строки части до того, как конец обхода его стёр. Без
    фиксации."""
    row = await always_depth.finish_part(
        session, run, report, base=part.base, own=own, now=datetime.now(UTC)
    )
    run.stats = {**(run.stats or {}), CHAIN_FROM: part.skip, CHAIN_TO: row.pages}
