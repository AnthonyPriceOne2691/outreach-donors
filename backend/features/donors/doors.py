"""Дверь сайта для авторов и рекламодателей: меню главной у очереди прогона.

Судья смотрит главную только у спорных доменов (`judging.DISPUTED`):
у остальных вердикт выносится по выдаче, и пункт меню «Advertise» или
«Write for us» не видно вовсе. А для гест-постинга это главный признак —
сайт сам продаёт размещение у себя. Поэтому после того, как прогон
положил кандидатов на рассмотрение, их главные смотрятся отдельно —
один бесплатный запрос на домен, — и дверь ложится на домен
(`DomainModel.site_door`). Очередь ставит таких первыми
(`review.candidates.sells_placement`).

**Смотрим только тех, про кого не знаем.** NULL — «не смотрели»;
пустая строка — «смотрели, двери нет». Повторный прогон и повторный
досуд главную второй раз не качают.

**Не открылась — не «двери нет».** Закрытая главная оставляет NULL:
про такой сайт мы не знаем ничего, и записать ему «двери нет» значило
бы поставить его ниже тех, у кого её точно нет, по выдумке. Сколько
не открылось — отдельное число в отчёте, а не отсутствие записи.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from backend.config import judge as judge_cfg
from backend.features.donors.author_door import author_door
from backend.features.donors.home_signals import HomeSignals, check_home
from backend.features.donors.repository import DonorRepository
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)

#: Сколько главных качать одновременно. Домены разные — ограничитель на хост
#: не нужен; потолок держит число соединений разумным.
CONCURRENCY = 8

#: Сколько доменов записывать в базу за раз. После каждой пачки — фиксация
#: (`checkpoint`): упавшая на середине проверка не теряет уже увиденное,
#: а повтор начинает с тех, кого ещё не смотрели.
CHUNK = 40


@dataclass(slots=True)
class DoorReport:
    """Что увидела проверка. Три числа, потому что новости три."""

    checked: int = 0
    found: int = 0
    unreached: int = 0
    #: Отказов «продаёт своё», которые дверь перевела к человеку.
    opened: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "found": self.found,
            "unreached": self.unreached,
            "opened": self.opened,
        }


FetchHome = Callable[[str], Awaitable[HomeSignals]]
Checkpoint = Callable[[], Awaitable[None]]


class DoorCheck:
    """Проверка дверей. Как качать главную — снаружи: прогон и команда
    досуда дают свой клиент, тесты — образцы страниц без сети."""

    def __init__(self, fetch_home: FetchHome, *, concurrency: int = CONCURRENCY) -> None:
        self._fetch_home = fetch_home
        self._limit = asyncio.Semaphore(concurrency)

    async def __call__(
        self,
        donors: DonorRepository,
        hosts: Sequence[str],
        *,
        checkpoint: Checkpoint | None = None,
    ) -> DoorReport:
        """Посмотреть меню главных у тех, про чью дверь не знаем.

        `checkpoint` фиксирует записанное после каждой пачки. Без него
        записанное ждёт фиксации вызывающего — так в тестах.
        """
        report = DoorReport()
        unknown = await donors.hosts_without_door(hosts)
        for start in range(0, len(unknown), CHUNK):
            chunk = unknown[start : start + CHUNK]
            homes = await asyncio.gather(*(self._home(host) for host in chunk))
            doors: dict[str, str] = {}
            for host, home in zip(chunk, homes, strict=True):
                report.checked += 1
                if not home.reached:
                    report.unreached += 1
                    continue
                door = author_door(None, None, home.nav) or ""
                report.found += bool(door)
                doors[host] = door
            report.opened += await donors.save_doors(doors)
            if checkpoint is not None:
                await checkpoint()
        logger.info(
            "двери сайтов: проверено %s, зовут авторов или рекламодателей %s, не открылись %s, "
            "отказов «продаёт своё» отдано человеку %s",
            report.checked,
            report.found,
            report.unreached,
            report.opened,
        )
        return report

    async def _home(self, host: str) -> HomeSignals:
        async with self._limit:
            return await self._fetch_home(host)


@asynccontextmanager
async def door_check() -> AsyncIterator[DoorCheck | None]:
    """Боевая проверка дверей: клиент для чужих сайтов, таймаут главной судьи.

    `None` — главные выключены настройкой (`JUDGE_HOME_CHECK`): тогда
    не смотрит их ни судья, ни очередь, и это одно решение, а не два.
    """
    if not judge_cfg.HOME_CHECK:
        yield None
        return
    async with guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC) as client:

        async def fetch(host: str) -> HomeSignals:
            return await check_home(client, host)

        yield DoorCheck(fetch)
