"""Ход долгого обхода: пачки по ходу, остановка на границе страницы, продолжение.

Обход в 1 000 страниц идёт до получаса (замер 06.10.2026: ~1,4 с на страницу),
и за это время воркер успевает пережить выкатку. До этого модуля обход отдавал
всё одним отчётом в конце: смерть процесса на 900-й странице теряла все 900,
а выкатка посреди обхода была именно смертью — докер добивает задачу через
десять секунд после просьбы остановиться.

Три правила, каждое — ответ на конкретную потерю:

**Ссылки уходят пачками по ходу**, вместе с чекпоинтом и одной транзакцией:
записанное и «с чего продолжить» не расходятся никогда.

**Остановка — на границе страницы.** Просьба остановиться (выкатка) не рвёт
страницу посередине: обход дописывает пачку и выходит с чекпоинтом
(`CrawlInterruptedError`) — это штатный исход, а не сбой.

**Продолжение не открывает открытое.** Открытые страницы и счётчики лежат
в чекпоинте; карта сайта читается заново (это несколько запросов), а очередь
обхода по ссылкам — из чекпоинта: собрать её заново значило бы обойти сайт
второй раз.

Без пульта (`Progress`) обход идёт как прежде: всё в памяти, запись в конце —
так работает консольный замер.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from backend.features.crawl.links import OutLink

logger = logging.getLogger(__name__)

#: Пачка — каждые столько открытых страниц. Смерть процесса теряет не больше
#: этого числа, а запись пачки — одна транзакция на ~200 ссылок.
BATCH_PAGES = 25

#: …или раз в столько секунд, если страницы идут медленно: сайт с паузой
#: в 10 с отдаёт 25 страниц за четыре минуты, и столько молчать незачем.
BATCH_SECONDS = 60.0


@dataclass(slots=True)
class Checkpoint:
    """С чего продолжить обход: всё, что нельзя получить заново даром."""

    source: str
    """Откуда адреса: `sitemap` или `links`."""
    pages: list[str] = field(default_factory=list)
    """Открытые страницы — их ссылки уже в базе, второй раз их не открываем."""
    queue: list[str] = field(default_factory=list)
    """Очередь обхода по ссылкам. У карты сайта пустая: карта читается заново."""
    attempts: int = 0
    articles: int = 0
    share_links: int = 0
    pages_labeled: int = 0
    pages_dated: int = 0
    elapsed_sec: float = 0.0
    """Сколько обход уже шёл: потолок времени — на донора, а не на попытку."""
    slowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def of(cls, data: Mapping[str, Any] | None) -> Checkpoint | None:
        """Чекпоинт из колонки. Испорченный — `None` с предупреждением:
        обход начнётся сначала, а не упадёт на каждой попытке."""
        if not data:
            return None
        known = {item.name for item in fields(cls)}
        try:
            return cls(**{key: value for key, value in data.items() if key in known})
        except TypeError as exc:
            logger.warning("обход: чекпоинт не прочитан (%s) — начну сначала", exc)
            return None


@dataclass(frozen=True, slots=True)
class Batch:
    """Новые ссылки с прошлой пачки и чекпоинт на этот момент."""

    links: list[OutLink]
    checkpoint: Checkpoint


Flush = Callable[[Batch], Awaitable[None]]


def _never() -> bool:
    return False


class CrawlInterruptedError(Exception):
    """Обход остановлен на границе страницы по просьбе — не сбой.

    Последняя пачка к этому моменту уже записана: продолжение начнётся
    с `checkpoint`, и ни одна открытая страница не откроется второй раз.
    """

    def __init__(self, checkpoint: Checkpoint) -> None:
        super().__init__(f"обход остановлен на {len(checkpoint.pages)} страницах — продолжится")
        self.checkpoint = checkpoint


@dataclass(slots=True)
class Progress:
    """Пульт обхода, который ведёт задача очереди.

    `flush` — куда уходит пачка; `resume` — с чего продолжить; `stop` —
    пора ли остановиться (спрашивается перед каждой страницей).
    """

    flush: Flush
    resume: Checkpoint | None = None
    stop: Callable[[], bool] = _never
    every_pages: int = BATCH_PAGES
    every_sec: float = BATCH_SECONDS
    _pages_sent: int = field(default=0, init=False)
    _sent_at: float = field(default_factory=time.monotonic, init=False)

    def due(self, pages: int) -> bool:
        """Пора ли отдавать пачку: набралось страниц или прошло время."""
        if pages - self._pages_sent >= self.every_pages:
            return True
        return time.monotonic() - self._sent_at >= self.every_sec

    async def send(self, links: list[OutLink], checkpoint: Checkpoint) -> None:
        await self.flush(Batch(links=links, checkpoint=checkpoint))
        self._pages_sent = len(checkpoint.pages)
        self._sent_at = time.monotonic()
