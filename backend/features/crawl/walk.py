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
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

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
from backend.features.crawl.robots import RobotsRules, RobotsStatus
from backend.features.crawl.sitemap import SitemapReader, SitemapScan

logger = logging.getLogger(__name__)

#: Расширения, за которыми страницы нет: качать их — это трафик без пользы.
SKIP_SUFFIXES: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".gz", ".rar", ".7z", ".tar",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".woff", ".woff2", ".ttf",
)  # fmt: skip


@dataclass(slots=True)
class CrawlReport:
    """Отчёт обхода: адреса, числа и всё, чего не хватило.

    Деградация лежит здесь, а не в логе, намеренно: иначе обход,
    у которого не поднялся браузер, выглядит зелёным и врёт, что
    закрытые страницы проверены.
    """

    host: str
    outcome: CrawlOutcome
    stop_reason: StopReason
    pages: list[str] = field(default_factory=list)
    links: list[OutLink] = field(default_factory=list)
    articles: int = 0
    robots_status: RobotsStatus = RobotsStatus.UNREADABLE
    crawl_delay: float | None = None
    sitemap_found: bool | None = False
    sitemap_complete: bool = False
    source: str = "none"  # откуда брались адреса: sitemap или ссылки
    elapsed_sec: float = 0.0
    slowed_down: bool = False
    health: dict[str, float | int] = field(default_factory=dict)
    by_level: dict[str, int] = field(default_factory=dict)
    degradation: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Плоская запись для колонки прогона и для отчёта замера."""
        return {
            "host": self.host,
            "outcome": self.outcome.value,
            "stop_reason": self.stop_reason.value,
            "pages_opened": len(self.pages),
            "articles": self.articles,
            "links_found": len(self.links),
            "advertisers": len({link.target_root for link in self.links}),
            "links_in_body": sum(1 for link in self.links if link.in_body),
            # Ссылки, у которых корень домена угадан: суффикс неизвестен
            # вшитому снимку. Ноль — норма, рост — повод обновить список.
            "roots_guessed": sum(1 for link in self.links if link.root_guessed),
            "robots": self.robots_status.value,
            "crawl_delay": self.crawl_delay,
            "sitemap_found": self.sitemap_found,
            "sitemap_complete": self.sitemap_complete,
            "source": self.source,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "slowed_down": self.slowed_down,
            "degradation": self.degradation,
            "by_level": self.by_level,
            **self.health,
        }


def _crawlable(url: str, host: str) -> bool:
    """Наш ли это адрес и страница ли это вообще."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
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
        absolute, _ = urldefrag(urljoin(page_url, href))
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

    async def crawl(self, host: str) -> CrawlReport:
        """Обойти донора. Единственный публичный вход."""
        host = host.lower().removeprefix("www.")
        started = time.monotonic()
        # Срок общий на всего донора, а не на обход страниц: robots и карты
        # тоже запросы, и на сайте с длинной паузой они одни съедают минуты.
        deadline = started + self._max_seconds
        rules = await self._robots(host)

        if not rules.readable:
            return self._report(host, CrawlOutcome.FAILED, StopReason.ROBOTS, rules, started)
        home = await self._base(host, rules)
        if home is None:
            return self._no_start(host, rules, started)

        # Главную качает проверка «сайт открывается», и обход до неё
        # уже не доходит — значит, снять с неё ссылки надо здесь. Иначе
        # у каждого донора теряется ровно одна страница, и та, где чаще
        # всего висит оффер.
        self._harvest(home.html or "", home.url, host)

        self._limiter.set_delay(host, rules.crawl_delay)
        scan = await self._sitemap(host, home.url, rules, deadline)
        queue, source, opened = self._queue(home, host, scan)
        pages, stop = await self._walk(
            host, rules, queue, started, opened=opened, follow=source == "links"
        )
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

    def _no_start(self, host: str, rules: RobotsRules, started: float) -> CrawlReport:
        """Главная не открылась. Исход — три разных, и путать их дорого.

        Запретил robots — донора смотрит человек. Закрылся антибот —
        вопрос решается следующим уровнем каскада, то есть деньгами.
        Не ответил вовсе — поломка, и покупать для неё прокси не за чем.
        """
        if not rules.allows(f"https://{host}/"):
            return self._report(host, CrawlOutcome.FORBIDDEN, StopReason.ROBOTS, rules, started)
        outcome = CrawlOutcome.BLOCKED if self._health.blocked_share > 0 else CrawlOutcome.FAILED
        return self._report(host, outcome, StopReason.NO_START, rules, started)

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
        self, home: FetchResult, host: str, scan: SitemapScan
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
        if scan.urls:
            return deque(scan.urls), "sitemap", [home.url]
        links = same_site_links(home.html or "", home.url, host)
        return deque(links), "links", [home.url]

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

        return pages, StopReason.EXHAUSTED

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
        анкор, пометки `rel` и домен-получатель.
        """
        if extract_article(html) is not None:
            self.articles += 1
        self.links.extend(harvest(html, page_url, host))

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
            robots_status=rules.status,
            crawl_delay=rules.crawl_delay,
            sitemap_found=scan.found if scan else False,
            sitemap_complete=scan.complete if scan else False,
            source=source,
            elapsed_sec=time.monotonic() - started,
            slowed_down=self._slowed,
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
