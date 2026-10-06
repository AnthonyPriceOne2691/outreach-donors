"""Обход одного донора: от robots.txt до списка открытых страниц.

Порядок работы здесь один и тот же для консоли, задачи очереди и будущего
экрана — иначе у кнопки и у команды разойдутся правила, и разойдутся они
молча (урок среза `operator-screens`).

**Сначала разрешение, потом страницы.** robots.txt читается до первого
запроса к страницам, и три его исхода дают три разных исхода обхода:
запрещено — донор откладывается человеку, не прочитан — обход не идёт
вовсе, разрешено — работаем. Пропустить этот шаг «ради замера» нельзя:
замер идёт по живым чужим сайтам.

**Список страниц берётся из карты сайта, а обход по ссылкам — запасной
путь.** Карта отдаёт статьи сразу; обход по ссылкам идёт кругами по
навигации и до статей доходит последним. Но карта есть не у всех,
и без запасного пути каждый пятый донор остался бы необойдённым.

**Сырой HTML не хранится** — это прямое требование. Страница живёт
ровно столько,
сколько нужно, чтобы снять с неё ссылки; сами ссылки — следующий срез.
Здесь наружу уходят только адреса и числа.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import replace
from urllib.parse import urldefrag

import httpx
from selectolax.parser import HTMLParser

from backend.config import crawl as cfg
from backend.features.contacts.browser import PageRenderer
from backend.features.contacts.pages import home_variants
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl import robots as robots_rules
from backend.features.crawl.article import extract_article
from backend.features.crawl.fetch import CascadeLevel, FetchOutcome, FetchResult, PageCascade
from backend.features.crawl.health import CrawlHealth, HealthVerdict
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.links import OutLink, harvest
from backend.features.crawl.page_facts import read_page
from backend.features.crawl.progress import Checkpoint, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.robots import RobotsRules
from backend.features.crawl.sitemap import SitemapReader, SitemapScan
from backend.shared.net.url_parts import join_url, parse_url

logger = logging.getLogger(__name__)

#: Расширения, за которыми страницы нет: качать их — это трафик без пользы.
SKIP_SUFFIXES: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".gz", ".rar", ".7z", ".tar",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".woff", ".woff2", ".ttf",
)  # fmt: skip


def _crawlable(url: str, host: str) -> bool:
    """Наш ли это адрес и страница ли это вообще. Битый адрес — не наш."""
    parts = parse_url(url)
    if parts is None or parts.scheme not in ("http", "https"):
        return False
    netloc = parts.netloc.lower().split(":")[0]
    if not (netloc == host or netloc.endswith(f".{host}")):
        return False
    return not parts.path.lower().endswith(SKIP_SUFFIXES)


def same_site_links(html: str, page_url: str, host: str) -> list[str]:
    """Ссылки со страницы на свой же сайт, без якорей и дублей.

    Якорь отбрасывается до сравнения: `/post` и `/post#comments` — одна
    страница, и без этого обход платит за неё столько раз, сколько
    на ней разделов.
    """
    out: list[str] = []
    seen: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        joined = join_url(page_url, href)
        if joined is None:
            continue
        absolute, _ = urldefrag(joined)
        if absolute in seen or not _crawlable(absolute, host):
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


class DonorCrawler:
    """Обход одного донора под четырьмя потолками и ограничителем.

    Собирается из готовых частей и ничего не решает за них: robots
    говорит «можно», ограничитель — «когда», каскад — «чем», здоровье —
    «не пора ли сбавить». Здесь только порядок и потолки.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        limiter: DomainLimiter | None = None,
        renderer: PageRenderer | None = None,
        use_browser: bool | None = None,
        identify: bool | None = None,
        max_pages: int | None = None,
        max_attempts: int | None = None,
        max_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter or DomainLimiter()
        self._cascade = PageCascade(
            client, renderer=renderer, browser_enabled=use_browser, identify=identify
        )
        self._health = CrawlHealth()
        self._max_pages = max_pages if max_pages is not None else cfg.MAX_PAGES_PER_DONOR
        self._max_attempts = (
            max_attempts if max_attempts is not None else cfg.MAX_ATTEMPTS_PER_DONOR
        )
        self._max_seconds = max_seconds if max_seconds is not None else cfg.MAX_SECONDS_PER_DONOR
        self._attempts = 0
        self._slowed = False
        self.articles = 0
        self.links: list[OutLink] = []
        self.share_links = 0
        self.pages_labeled = 0
        self.pages_dated = 0
        self._progress: Progress | None = None
        self._sent = 0
        self._where: tuple[str, float] = ("none", time.monotonic())
        self._was_slowed = False

    async def crawl(self, host: str, progress: Progress | None = None) -> CrawlReport:
        """Обойти донора. Единственный публичный вход.

        `progress` — пульт задачи очереди: пачки по ходу, остановка
        и продолжение (`crawl/progress.py`). У консоли его нет — всё
        в памяти и в конце, как прежде.
        """
        host = host.lower().removeprefix("www.")
        point = self._resume(progress)
        # Продолжение считает время с прошлых попыток: потолок — на донора.
        started = time.monotonic() - point.elapsed_sec
        # Срок общий на всего донора, а не на обход страниц: robots и карты
        # тоже запросы, и на сайте с длинной паузой они одни съедают минуты.
        deadline = started + self._max_seconds
        done = list(point.pages)
        rules = await self._robots(host)

        if not rules.readable:
            stop = StopReason.ROBOTS
            return self._report(host, self._outcome(done, stop), stop, rules, started, pages=done)
        home = await self._base(host, rules)
        if home is None:
            return self._no_start(host, rules, started, done)

        # Главную качает проверка «сайт открывается», и обход до неё
        # уже не доходит — значит, снять с неё ссылки надо здесь. Иначе
        # у каждого донора теряется ровно одна страница, и та, где чаще
        # всего висит оффер. У продолжения она уже снята.
        if home.url not in done:
            self._harvest(home.html or "", home.url, host)

        self._limiter.set_delay(host, rules.crawl_delay)
        scan = await self._sitemap(host, home.url, rules, deadline)
        queue, source, opened = self._queue(home, host, scan, point)
        self._where = (source, started)
        pages, stop = await self._walk(
            host, rules, queue, started, opened=opened, follow=source == "links"
        )
        await self._send(pages, queue)
        return self._report(
            host,
            self._outcome(pages, stop),
            stop,
            rules,
            started,
            pages=pages,
            scan=scan,
            source=source,
        )

    def _resume(self, progress: Progress | None) -> Checkpoint:
        """С чего начать: чекпоинт продолжения или чистый лист. Счётчики
        продолжения — с прошлых попыток: обход один, попыток несколько."""
        self._progress = progress
        point = (progress.resume if progress is not None else None) or Checkpoint("none")
        self._attempts, self._was_slowed = point.attempts, point.slowed
        self.articles, self.share_links = point.articles, point.share_links
        self.pages_labeled, self.pages_dated = point.pages_labeled, point.pages_dated
        return point

    def _no_start(
        self, host: str, rules: RobotsRules, started: float, done: list[str]
    ) -> CrawlReport:
        """Главная не открылась. Исход — три разных, и путать их дорого.

        Запретил robots — донора смотрит человек. Закрылся антибот —
        вопрос решается следующим уровнем каскада, то есть деньгами.
        Не ответил вовсе — поломка, и покупать для неё прокси не за чем.
        """
        if not rules.allows(f"https://{host}/"):
            forbidden = CrawlOutcome.FORBIDDEN
            return self._report(host, forbidden, StopReason.ROBOTS, rules, started, pages=done)
        stop = StopReason.NO_START
        return self._report(host, self._outcome(done, stop), stop, rules, started, pages=done)

    async def _robots(self, host: str) -> RobotsRules:
        """robots.txt в первом виде хоста, который ответил."""
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}/robots.txt"
            async with self._limiter.slot(host):
                try:
                    response = await self._client.get(
                        url, follow_redirects=True, timeout=cfg.ROBOTS_TIMEOUT_SEC
                    )
                except httpx.HTTPError as exc:
                    logger.info("robots: %s не открылся (%r)", url, exc)
                    continue
            if response.status_code in (404, 410):
                return robots_rules.absent()
            if response.status_code >= 400:
                logger.info("robots: %s ответил %s", url, response.status_code)
                continue
            text = response.text[: robots_rules.MAX_ROBOTS_BYTES]
            return robots_rules.parse_robots(text, cfg.USER_AGENT_TOKEN)

        logger.warning(
            "обход %s не начат: robots.txt не прочитан. Молчание сайта — не согласие; "
            "донор остаётся непроверенным, а не пустым",
            host,
        )
        return robots_rules.unreadable()

    async def _base(self, host: str, rules: RobotsRules) -> FetchResult | None:
        """Главная в первом виде, который ответил, — вместе с её телом.

        Возвращается весь ответ, а не адрес: без карты сайта очередь
        собирается из ссылок этой же страницы, и качать её второй раз
        значит лишний запрос к чужой машине на каждом доноре.
        """
        for url in home_variants(host):
            if not rules.allows(url):
                logger.info("обход %s: robots.txt запрещает корень — донор откладывается", host)
                return None
            async with self._limiter.slot(host):
                result = await self._cascade.get(url)
            self._attempts += 1
            self._health.record(result.outcome)
            if result.outcome is FetchOutcome.OK:
                return result
        return None

    async def _sitemap(
        self, host: str, base: str, rules: RobotsRules, deadline: float
    ) -> SitemapScan:
        reader = SitemapReader(self._client, self._limiter)
        return await reader.scan(host, base, rules.sitemaps, deadline=deadline)

    def _queue(
        self, home: FetchResult, host: str, scan: SitemapScan, point: Checkpoint
    ) -> tuple[deque[str], str, list[str]]:
        """Очередь адресов, источник списка и уже открытые страницы.

        Из карты сайта — обход не ходит по ссылкам вовсе: адреса уже
        есть, и каждая лишняя страница стоит запроса к чужой машине.
        Без карты очередь собирается со скачанной главной, а сама она
        считается открытой: второй раз её качать не за чем.
        """
        # Главная открыта в любом случае — её качала проверка «сайт
        # открывается». Она же и первая страница отчёта, откуда бы
        # ни взялась очередь: иначе статей окажется больше, чем страниц.
        # Продолжение начинает с уже открытых, а очередь по ссылкам
        # берёт из чекпоинта: собрать её заново — обойти сайт второй раз.
        opened = list(point.pages) or [home.url]
        if point.source == "links":
            return deque(point.queue), "links", opened
        if scan.urls:
            return deque(scan.urls), "sitemap", opened
        links = same_site_links(home.html or "", home.url, host)
        return deque(links), "links", opened

    async def _walk(
        self,
        host: str,
        rules: RobotsRules,
        queue: deque[str],
        started: float,
        *,
        opened: list[str],
        follow: bool,
    ) -> tuple[list[str], StopReason]:
        """Собственно обход. Возвращает открытые страницы и причину конца."""
        pages = list(opened)
        seen: set[str] = set(opened) | set(queue)
        # Уже открытое не открывается второй раз. Карта сайта почти всегда
        # перечисляет главную, а её качает проверка «сайт открывается»:
        # без этой проверки донор получает лишний запрос, а отчёт —
        # лишнюю статью, которой не было.
        visited: set[str] = set(opened)

        while queue:
            stop = self._limit_hit(pages, started)
            if stop is not None:
                return pages, stop
            await self._stop_if_asked(pages, queue)

            url = queue.popleft()
            if url in visited or not rules.allows(url):
                continue
            visited.add(url)

            async with self._limiter.slot(host):
                result = await self._cascade.get(url)
            self._attempts += 1
            self._health.record(result.outcome)
            self._react(host)

            if result.outcome is not FetchOutcome.OK or result.html is None:
                continue
            pages.append(result.url)
            self._harvest(result.html, result.url, host)
            if follow:
                self._enqueue(result, host, queue, seen)
            await self._send_if_due(pages, queue)

        return pages, StopReason.EXHAUSTED

    async def _send_if_due(self, pages: list[str], queue: deque[str]) -> None:
        """Пачка по ходу — если обход ведёт задача очереди и пора."""
        if self._progress is not None and self._progress.due(len(pages)):
            await self._send(pages, queue)

    async def _stop_if_asked(self, pages: list[str], queue: deque[str]) -> None:
        """Просьба остановиться (выкатка) — на границе страницы: пачка
        дописывается, и обход выходит с чекпоинтом, а не обрывается."""
        if self._progress is None or not self._progress.stop():
            return
        raise CrawlInterruptedError(await self._send(pages, queue))

    async def _send(self, pages: list[str], queue: deque[str]) -> Checkpoint:
        """Ссылки с прошлой пачки и чекпоинт — тому, кто ведёт обход."""
        source, started = self._where
        # Очередь по ссылкам — не длиннее оставшихся попыток: больше не открыть.
        left = max(self._max_attempts - self._attempts, 0)
        checkpoint = Checkpoint(
            source=source,
            pages=list(pages),
            queue=list(queue)[:left] if source == "links" else [],
            attempts=self._attempts,
            articles=self.articles,
            share_links=self.share_links,
            pages_labeled=self.pages_labeled,
            pages_dated=self.pages_dated,
            elapsed_sec=round(time.monotonic() - started, 1),
            slowed=self._slowed or self._was_slowed,
        )
        if self._progress is not None:
            fresh = self.links[self._sent :]
            await self._progress.send(fresh, checkpoint)
            self._sent += len(fresh)
        return checkpoint

    def _harvest(self, html: str, page_url: str, host: str) -> None:
        """Внешние ссылки страницы с пометкой «в теле статьи или вне».

        Страница без тела не ошибка: раздел со списком и карточка товара
        статьями не являются. Но она и не «страница без ссылок» — разница
        видна по счётчику статей рядом с числом открытых страниц.

        Почему не только из тела, хотя требование говорит именно так, —
        в `links.harvest`: замер показал, что размещения этой ниши живут
        в витринах офферов рядом со статьёй, и буква требования оставила
        бы нас без рекламодателей вовсе.

        Сырой HTML при этом никуда не уезжает: наружу выходят адрес,
        анкор, пометки `rel` и домен-получатель — и то, что статья
        сказала о себе (`page_facts`): пометка рекламы и дата выхода.
        """
        article = extract_article(html)
        if article is not None:
            self.articles += 1
        facts = read_page(html, article.text if article is not None else None)
        self.pages_labeled += facts.label is not None
        self.pages_dated += facts.published is not None
        shares: set[str] = set()
        self.links.extend(
            replace(link, page_label=facts.label, page_published=facts.published)
            for link in harvest(html, page_url, host, shares=shares)
        )
        self.share_links += len(shares)

    def _enqueue(self, result: FetchResult, host: str, queue: deque[str], seen: set[str]) -> None:
        """Ссылки со страницы — в очередь, каждая по одному разу."""
        if result.html is None:
            return
        for link in same_site_links(result.html, result.url, host):
            if link not in seen:
                seen.add(link)
                queue.append(link)

    def _limit_hit(self, pages: list[str], started: float) -> StopReason | None:
        """Какой потолок кончился первым. `None` — можно продолжать."""
        if len(pages) >= self._max_pages:
            return StopReason.MAX_PAGES
        if self._attempts >= self._max_attempts:
            return StopReason.MAX_ATTEMPTS
        if time.monotonic() - started >= self._max_seconds:
            return StopReason.TIMEOUT
        if self._health.verdict is HealthVerdict.STOP:
            return StopReason.UNHEALTHY
        return None

    def _react(self, host: str) -> None:
        """Ответ на просевшее здоровье: вдвое медленнее, и один раз."""
        if self._slowed or self._health.verdict is not HealthVerdict.SLOW_DOWN:
            return
        delay = self._limiter.slow_down(host)
        self._slowed = True
        logger.warning(
            "обход %s: отказов %.0f%% за последние запросы — пауза увеличена до %.1f с",
            host,
            self._health.failure_share * 100,
            delay,
        )

    #: Причины, по которым обход считается доведённым до конца: список
    #: адресов кончился или кончился потолок страниц, которые мы и просили.
    _COMPLETE = frozenset({StopReason.EXHAUSTED, StopReason.MAX_PAGES})

    def _outcome(self, pages: list[str], stop: StopReason) -> CrawlOutcome:
        """Исход обхода по тому, что получилось, а не по тому, что хотели.

        `partial` отделён от `ok` не из педантизма. Сайт, попросивший
        паузу в десять секунд, за отведённое время отдаёт одну страницу
        из тысячи — назвать это «обойдён» значит потом считать по нему
        рекламодателей так, будто видели весь сайт. «Не проверен»
        и «проверен» — разные состояния, и это ровно тот случай.
        """
        if not pages:
            return CrawlOutcome.BLOCKED if self._health.blocked_share > 0 else CrawlOutcome.FAILED
        return CrawlOutcome.OK if stop in self._COMPLETE else CrawlOutcome.PARTIAL

    def _report(
        self,
        host: str,
        outcome: CrawlOutcome,
        stop: StopReason,
        rules: RobotsRules,
        started: float,
        *,
        pages: list[str] | None = None,
        scan: SitemapScan | None = None,
        source: str = "none",
    ) -> CrawlReport:
        return CrawlReport(
            host=host,
            outcome=outcome,
            stop_reason=stop,
            pages=pages or [],
            links=list(self.links),
            articles=self.articles,
            share_links=self.share_links,
            pages_labeled=self.pages_labeled,
            pages_dated=self.pages_dated,
            robots_status=rules.status,
            crawl_delay=rules.crawl_delay,
            sitemap_found=scan.found if scan else False,
            sitemap_complete=scan.complete if scan else False,
            source=source,
            elapsed_sec=time.monotonic() - started,
            slowed_down=self._slowed or self._was_slowed,
            health=self._health.report(),
            by_level={
                level.value: count
                for level, count in self._cascade.by_level.items()
                if count or level is not CascadeLevel.HTTP
            },
            degradation=self._cascade.degradation.as_dict(),
        )

    async def aclose(self) -> None:
        await self._cascade.aclose()
