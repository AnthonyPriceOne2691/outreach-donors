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

**Свежие — первыми.** Размещения покупают сейчас, а потолок страниц
меньше сайта: в каком порядке карта отдала адреса, в таком обход
их и откроет. Поэтому и дочерние карты индекса, и адреса страниц
идут по дате (`<lastmod>`, у новостной карты — `<news:publication_date>`),
от новой к старой; без даты — после датированных, в порядке карты.
Дата правки — не дата выхода: бывает, что сайт обновил её у всех
статей разом. Настоящая дата статьи снимается со страницы (`page_facts`).

**Где искать карту, если robots.txt молчит** (29.10.2026: «может, она есть, а мы не там
смотрим»). Сначала — что говорит главная: `<link rel="sitemap">` и ссылки на карту в подвале
(`declared_in_html`, страница уже скачана — поиск бесплатный). Потом типичные адреса движков
(`COMMON_PATHS`): до первой настоящей карты — остальные адреса того же сайта дали бы то же.
**Настоящая — это карта, а не любой ответ 200**: сайт, который на любой адрес отвечает
страницей «не найдено» со статусом 200, раньше «находил карту» на первой же догадке, и
поиск на этом кончался. Карта — XML с `<urlset>` или `<sitemapindex>` или текстовый файл
(формат, который понимают поисковики: адрес на строку). Объявленные в robots.txt читаются,
как прежде, все и как есть: сайт сам сказал, что это карта. Нет карты вовсе — свежие статьи
даёт лента сайта (`feeds.py`).
"""

from __future__ import annotations

import gzip
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from backend.config import crawl as cfg
from backend.features.crawl.limiter import DomainLimiter
from backend.shared.net.url_parts import join_url, parse_url

logger = logging.getLogger(__name__)

#: Где sitemap лежит, если robots.txt о нём молчит, — от частых к редким: первая настоящая
#: карта кончает поиск. Общие (WordPress с плагинами, Drupal, Shopify, Tilda, Wix, Ghost —
#: `/sitemap.xml`; Yoast и Rank Math — индекс `/sitemap_index.xml`; WordPress без плагинов —
#: `/wp-sitemap.xml`), затем генераторы с номерами и во множественном числе, каталог и
#: текстовый формат поисковиков, затем новостные карты и карта записей Yoast и Rank Math —
#: для сайта, у которого их индекс закрыт, а сами карты открыты.
COMMON_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap.xml.gz",
    "/sitemap1.xml",
    "/sitemaps.xml",
    "/sitemap/",
    "/sitemap.txt",
    "/news-sitemap.xml",
    "/sitemap-news.xml",
    "/post-sitemap.xml",
)

#: Ссылка на карту с главной — только на файл карты: страница «Карта сайта» в HTML — обычная
#: страница, её откроет обход по ссылкам.
_MAP_SUFFIXES = (".xml", ".xml.gz", ".txt")
#: Сколько карт, объявленных главной, пробуем: больше — это ссылки на карты разделов, их
#: перечислит индекс.
MAX_HTML_DECLARED = 5

#: Префикс пространства имён у элемента карты: генераторы пишут и `<urlset>`, и
#: `<ns0:urlset><ns0:url><ns0:loc>` (так XML выдаёт, например, стандартная библиотека Python),
#: и `<sitemap:sitemapindex>`. Без него карта такого сайта не узнавалась бы картой, а её
#: адреса и даты не читались бы вовсе.
NS_PREFIX = r"(?:[\w.-]+:)?"
_LOC = re.compile(
    rf"<{NS_PREFIX}loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)\s*(?:\]\]>)?\s*</{NS_PREFIX}loc>",
    re.IGNORECASE,
)
_IS_INDEX = re.compile(rf"<{NS_PREFIX}sitemapindex\b", re.IGNORECASE)
_IS_MAP = re.compile(rf"<{NS_PREFIX}(?:urlset|sitemapindex)\b", re.IGNORECASE)
#: Строка текстовой карты — один адрес и ничего больше.
_TEXT_LINE = re.compile(r"^https?://\S+$", re.IGNORECASE)
#: Запись карты целиком — чтобы сопоставить адрес с его датой. Адреса
#: по-прежнему берутся `_LOC`: запись с битой разметкой даты не даст,
#: но адрес из неё не потеряется. Имя записи — целиком (`(?=[\s/>])`): иначе
#: `<sitemap:sitemapindex>` читался бы записью `<sitemap>`.
_ENTRY = re.compile(
    rf"<{NS_PREFIX}(?:url|sitemap)(?=[\s/>])[^>]*>(.*?)</{NS_PREFIX}(?:url|sitemap)>",
    re.IGNORECASE | re.DOTALL,
)
_DAY = re.compile(
    rf"<{NS_PREFIX}(?:lastmod|publication_date)>\s*(?:<!\[CDATA\[)?\s*(\d{{4}}-\d{{2}}-\d{{2}})",
    re.IGNORECASE,
)

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
    #: День записи карты по адресу (`YYYY-MM-DD`): по нему адреса идут
    #: от свежих к старым.
    days: dict[str, str] = field(default_factory=dict)

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


def _days(text: str) -> dict[str, str]:
    """Адрес записи → её день. Записи без даты в словарь не попадают."""
    days: dict[str, str] = {}
    for entry in _ENTRY.finditer(text):
        loc, day = _LOC.search(entry.group(1)), _DAY.search(entry.group(1))
        if loc is not None and day is not None:
            days[loc.group(1).strip()] = day.group(1)
    return days


def freshest_first(urls: list[str], days: dict[str, str]) -> list[str]:
    """Сначала датированные, от новой к старой; без даты — после, в порядке карты.

    Сортировка устойчивая: адреса с одной датой (индекс, где у всех
    файлов одна дата сборки) остаются в порядке, в котором их дал сайт.
    """
    return sorted(urls, key=lambda url: (url in days, days.get(url, "")), reverse=True)


def same_site(url: str, host: str) -> bool:
    """Карта чужого сайта — либо ошибка, либо подстава: страницы чужого
    домена нам не нужны, а ходить по ним по просьбе донора тем более.
    То же правило — у ленты (`feeds.py`): агрегатор в ленте донора — не донор."""
    parts = parse_url(url)
    # Схема — тоже признак чужого: `ftp://site.com/` и опечатка `hhttps://`
    # проходили сюда и отпадали только в очереди обхода (ревью #148).
    if parts is None or parts.scheme not in ("http", "https"):
        return False
    netloc = (parts.hostname or "").lower()
    # Сравнение через `endswith(host)` было бы дырой: `notexample.com`
    # кончается на `example.com`, и карта чужого сайта прошла бы проверку.
    return netloc == host or netloc.endswith(f".{host}")


def _text_urls(text: str) -> list[str]:
    """Адреса текстовой карты — по адресу на строку. Разметка в начале файла — это HTML или
    XML, а не текстовая карта: страница «не найдено» с сотней ссылок картой не станет."""
    if "<" in text[:512]:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in lines if _TEXT_LINE.match(line)]


def looks_like_map(text: str) -> bool:
    """Карта ли это, а не страница «не найдено» со статусом 200: XML карты или индекса — или
    текстовая карта."""
    return bool(_IS_MAP.search(text)) or bool(_text_urls(text))


def declared_in_html(html: str, base: str, host: str) -> list[str]:
    """Карты, на которые ссылается главная: `<link rel="sitemap">` и ссылки на файл карты
    (`/sitemap.xml` в подвале и т. п.) — свои, без повторов, не больше `MAX_HTML_DECLARED`."""
    out: list[str] = []
    for node in HTMLParser(html).css("link[href], a[href]"):
        url = _map_link(node.tag, node.attributes, base)
        if url is not None and url not in out and same_site(url, host):
            out.append(url)
    return out[:MAX_HTML_DECLARED]


def _map_link(tag: str, attributes: Mapping[str, str | None], base: str) -> str | None:
    """Адрес карты из `<link rel="sitemap">` или из ссылки на файл карты; иначе `None`."""
    href = (attributes.get("href") or "").strip()
    if not href:
        return None
    url = join_url(base, href)
    if tag == "link":
        return url if "sitemap" in (attributes.get("rel") or "").lower().split() else None
    parts = parse_url(url) if url is not None else None
    path = parts.path.lower() if parts is not None else ""
    return url if "sitemap" in path and path.endswith(_MAP_SUFFIXES) else None


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
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        # Заголовки страниц обхода (`PageCascade.headers`): карту просит то же имя.
        self._headers = dict(headers or {})
        self._max_files = max_files if max_files is not None else cfg.MAX_SITEMAP_FILES
        self._max_urls = max_urls if max_urls is not None else cfg.MAX_SITEMAP_URLS

    async def scan(
        self,
        host: str,
        base: str,
        declared: list[str],
        *,
        deadline: float | None = None,
        hinted: Sequence[str] = (),
    ) -> SitemapScan:
        """Карты из robots.txt, а если их нет — объявленные главной (`hinted`,
        `declared_in_html`) и типичные адреса.

        `deadline` — общий срок на донора. Без него потолок времени
        не покрывал бы чтение карт вовсе: сайт с паузой в десять секунд
        и девятнадцатью объявленными картами съедал бы три минуты
        до первой открытой страницы, и срок не срабатывал ни разу.
        Найдено живым прогоном.
        """
        scan = SitemapScan()
        guesses = [urljoin(base, path) for path in COMMON_PATHS]
        queue = list(declared) or list(dict.fromkeys([*hinted, *guesses]))
        seen: set[str] = set()
        # Объявленные в robots.txt пробуем все и как есть; без них ищем — до первой
        # настоящей карты: остальные догадки того же сайта дадут то же самое. Объявленные
        # главной читаются все — снимаются только догадки.
        searching = not declared
        guessed = set(guesses) - set(hinted)
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
            if url in seen or not same_site(url, host):
                continue
            seen.add(url)
            tried += 1

            text = await self._read(host, url, scan)
            if text is None:
                continue
            if searching:
                if not looks_like_map(text):
                    # Ответ 200, но не карта — страница «не найдено» или HTML: ищем дальше.
                    logger.info("sitemap: %s ответил, но это не карта — ищу дальше", url)
                    continue
                # Один раз: убираются только угаданные адреса. Найдено тестом
                # 06.10: очередь чистилась после КАЖДОГО прочитанного файла,
                # и из индекса читался только первый дочерний — у донора
                # с 75 картами обход видел одну новостную.
                queue[:] = [item for item in queue if item not in guessed]
                searching = False
            scan.files_read += 1
            self._absorb(text, scan, queue, host)

        # Прочитанный файл без адресов — это всё-таки прочитанная карта:
        # у сайта просто нет страниц в ней, и это знание, а не пустота.
        if scan.files_read and scan.found is not None:
            scan.found = True
        if queue:
            scan.truncated = True
        scan.urls = freshest_first(scan.urls, scan.days)
        return scan

    async def _read(self, host: str, url: str, scan: SitemapScan) -> str | None:
        """Один файл карты. Отказ сети — это «не дочитали», а не «нет»."""
        async with self._limiter.slot(host):
            try:
                response = await self._client.get(url, headers=self._headers, follow_redirects=True)
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

    def _absorb(self, text: str, scan: SitemapScan, queue: list[str], host: str) -> None:
        """Разложить файл: индекс — в очередь файлов, карта — в адреса сайта. Текстовая
        карта — адрес на строку, без дат."""
        locations = [match.group(1).strip() for match in _LOC.finditer(text)] or _text_urls(text)
        if not locations:
            logger.info("sitemap: файл без единого <loc> — разметка не та, что ожидали")
            return
        days = _days(text)

        if _IS_INDEX.search(text):
            queue.extend(freshest_first(locations, days))
            return

        # Чужой или битый адрес страницы в очередь не идёт: по чужому сайту
        # обход ходил бы под слотом донора, а битый («[» без пары) ронял
        # разбор разрешений robots и с ним весь обход (ревью e6 #148).
        # Дочерние карты ту же проверку проходят в `scan`, выходя из очереди.
        pages = [url for url in locations if same_site(url, host)]
        if len(pages) < len(locations):
            logger.info(
                "sitemap: %s адресов не с сайта или не разбираются — пропущены",
                len(locations) - len(pages),
            )

        # Свежие — до среза потолком: иначе потолок отрезал бы как раз их,
        # если карта перечисляет статьи от старых к новым.
        pages = freshest_first(pages, days)
        room = self._max_urls - len(scan.urls)
        if len(pages) > room:
            scan.truncated = True
        kept = pages[:room]
        scan.urls.extend(kept)
        scan.days.update((url, days[url]) for url in kept if url in days)
