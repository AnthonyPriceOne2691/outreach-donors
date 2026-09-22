"""Sitemap: откуда берётся список страниц донора.

Обход по ссылкам с главной работает, но идёт кругами по навигации
и находит статьи последними. Sitemap отдаёт то же самое сразу и без
запросов к сайту за каждой промежуточной страницей.

**Три вещи, которые здесь легко сделать неправильно.**

1. **«Не нашли» — не то же, что «не дочитали».** Сайт без sitemap —
   обычное дело, и это `False`. Сайт, чей sitemap оборвался на середине
   или ответил пятисотым, — это `None`: мы не знаем, сколько у него
   страниц, и делать вид, что знаем, значит объявить обход полным,
   обойдя четверть.
2. **Индекс ссылается на индексы.** Крупный сайт отдаёт индекс с полусотней
   файлов, каждый из которых бывает индексом. Без потолка на число файлов
   обход утонет в них, не открыв ни одной страницы.
3. **XML читается выражением, а не разбором.** Чужой XML — это вход
   от постороннего: разборщик из стандартной библиотеки раскрывает
   сущности, и файл в сто килобайт разворачивается в гигабайты в памяти.
   Нам нужны только адреса из `<loc>`, и выражение достаёт их, ничего
   не раскрывая. Заодно оно переживает сломанную разметку, которой
   у доноров хватает.
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import crawl as cfg
from backend.features.crawl.limiter import DomainLimiter

logger = logging.getLogger(__name__)

#: Где sitemap лежит, если robots.txt о нём молчит.
COMMON_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap.xml.gz",
)

_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)\s*(?:\]\]>)?\s*</loc>", re.IGNORECASE)
_IS_INDEX = re.compile(r"<sitemapindex", re.IGNORECASE)

#: Сколько байт одного файла читаем. Карта на десятки мегабайт бывает,
#: но потолок адресов всё равно кончится раньше.
MAX_SITEMAP_BYTES = 20 * 1024 * 1024


@dataclass(slots=True)
class SitemapScan:
    """Что удалось снять с карт сайта.

    `found` — трёхзначное намеренно: `True` — карта есть и дочитана,
    `False` — карты нет, `None` — карта есть, но дочитать не удалось.
    """

    urls: list[str] = field(default_factory=list)
    found: bool | None = False
    files_read: int = 0
    truncated: bool = False

    @property
    def complete(self) -> bool:
        """Можно ли считать список страниц полным."""
        return self.found is True and not self.truncated


def _decode(response: httpx.Response, url: str) -> str | None:
    """Текст карты, включая упакованную. Не распаковалась — не молчим."""
    body = response.content[:MAX_SITEMAP_BYTES]
    if url.endswith(".gz") or response.headers.get("content-type", "").endswith("gzip"):
        try:
            return gzip.decompress(body).decode("utf-8", errors="replace")
        except (OSError, EOFError) as exc:
            logger.warning("sitemap: %s не распаковался (%r)", url, exc)
            return None
    return response.text


def _same_site(url: str, host: str) -> bool:
    """Карта чужого сайта — либо ошибка, либо подстава: страницы чужого
    домена нам не нужны, а ходить по ним по просьбе донора тем более."""
    netloc = urlparse(url).netloc.lower().split(":")[0]
    # Сравнение через `endswith(host)` было бы дырой: `notexample.com`
    # кончается на `example.com`, и карта чужого сайта прошла бы проверку.
    return netloc == host or netloc.endswith(f".{host}")


class SitemapReader:
    """Читает карты сайта одного донора, соблюдая оба потолка.

    Потолков два, потому что они про разное: файлы — про глубину
    вложенных индексов, адреса — про объём работы, который мы готовы
    взять. Упёршись в любой, помечаем список неполным, а не молчим.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: DomainLimiter,
        *,
        max_files: int | None = None,
        max_urls: int | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._max_files = max_files if max_files is not None else cfg.MAX_SITEMAP_FILES
        self._max_urls = max_urls if max_urls is not None else cfg.MAX_SITEMAP_URLS

    async def scan(
        self, host: str, base: str, declared: list[str], *, deadline: float | None = None
    ) -> SitemapScan:
        """Карты из robots.txt, а если их нет — по типичным адресам.

        `deadline` — общий срок на донора. Без него потолок времени
        не покрывал бы чтение карт вовсе: сайт с паузой в десять секунд
        и девятнадцатью объявленными картами съедал бы три минуты
        до первой открытой страницы, и срок не срабатывал ни разу.
        Найдено живым прогоном.
        """
        scan = SitemapScan()
        queue = list(declared) or [urljoin(base, path) for path in COMMON_PATHS]
        seen: set[str] = set()
        # Объявленные в robots.txt пробуем все; угаданные — до первой
        # ответившей: остальные адреса того же сайта дадут то же самое.
        stop_on_first = not declared
        # Потолок считает **попытки**, а не удачи. Найдено живым прогоном:
        # индекс настоящего сайта перечислял полсотни дочерних карт, и все
        # отвечали 404. Счётчик прочитанных не рос, потолок не срабатывал,
        # и обход двадцать минут ходил по несуществующим адресам, не открыв
        # ни одной страницы. Отказ — тоже запрос к чужой машине.
        tried = 0

        while queue and tried < self._max_files and len(scan.urls) < self._max_urls:
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("sitemap: срок на донора вышел, карты дочитаны не все")
                break
            url = queue.pop(0)
            if url in seen or not _same_site(url, host):
                continue
            seen.add(url)
            tried += 1

            text = await self._read(host, url, scan)
            if text is None:
                continue
            scan.files_read += 1
            if stop_on_first:
                queue.clear()
            self._absorb(text, scan, queue)

        # Прочитанный файл без адресов — это всё-таки прочитанная карта:
        # у сайта просто нет страниц в ней, и это знание, а не пустота.
        if scan.files_read and scan.found is not None:
            scan.found = True
        if queue:
            scan.truncated = True
        return scan

    async def _read(self, host: str, url: str, scan: SitemapScan) -> str | None:
        """Один файл карты. Отказ сети — это «не дочитали», а не «нет»."""
        async with self._limiter.slot(host):
            try:
                response = await self._client.get(url, follow_redirects=True)
            except httpx.HTTPError as exc:
                logger.info("sitemap: %s не открылся (%r)", url, exc)
                scan.found = None
                return None

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            logger.info("sitemap: %s ответил %s", url, response.status_code)
            scan.found = None
            return None
        return _decode(response, url)

    def _absorb(self, text: str, scan: SitemapScan, queue: list[str]) -> None:
        """Разложить файл: индекс — в очередь файлов, карта — в адреса."""
        locations = [match.group(1).strip() for match in _LOC.finditer(text)]
        if not locations:
            logger.info("sitemap: файл без единого <loc> — разметка не та, что ожидали")
            return

        if _IS_INDEX.search(text):
            queue.extend(locations)
            return

        room = self._max_urls - len(scan.urls)
        if len(locations) > room:
            scan.truncated = True
        scan.urls.extend(locations[:room])
