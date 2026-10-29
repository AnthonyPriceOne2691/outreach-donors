"""Обход одного донора: от robots.txt до списка открытых страниц.

Порядок работы здесь один и тот же для консоли, задачи очереди и будущего
экрана — иначе у кнопки и у команды разойдутся правила, и разойдутся они
молча (урок среза `operator-screens`).

**Сначала разрешение, потом страницы.** robots.txt читается до первого
запроса к страницам, и три его исхода дают три разных исхода обхода:
запрещено — донор откладывается человеку, не прочитан — обход не идёт
вовсе, разрешено — работаем. Пропустить этот шаг «ради замера» нельзя:
замер идёт по живым чужим сайтам.

**Сначала карта сайта, потом ссылки** (`frontier.py`). Карта отдаёт статьи
сразу; обход по ссылкам идёт кругами по навигации и до статей доходит
последним. Но карта есть не у всех, а бывает и короче потолка: без ссылок
каждый пятый донор остался бы необойдённым, а сайт с короткой картой —
обойдённым наполовину.

**Срок — по паузе сайта** (`pace.py`): час на всех не вмещал сайту с долгой
паузой и половины потолка страниц; вышел — продлевается по темпу обхода. Где брать
адреса — карта, лента, ссылки главной — решает `sources.py`.

**Сырой HTML не хранится** — это прямое требование. Страница живёт
ровно столько,
сколько нужно, чтобы снять с неё ссылки; сами ссылки — следующий срез.
Здесь наружу уходят только адреса и числа.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

import httpx

from backend.config import crawl as cfg
from backend.features.contacts.pages import home_variants
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl import robots as robots_rules
from backend.features.crawl.article import extract_article
from backend.features.crawl.fetch import CascadeLevel, FetchOutcome, FetchResult, PageCascade
from backend.features.crawl.frontier import Frontier, same_site_links
from backend.features.crawl.health import CrawlHealth, HealthVerdict
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.links import OutLink, harvest
from backend.features.crawl.pace import ResponseClock, extended, time_budget
from backend.features.crawl.page_facts import read_page
from backend.features.crawl.progress import Checkpoint, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.robots import RobotsRules
from backend.features.crawl.sitemap import SitemapScan
from backend.features.crawl.sources import read_sources, start_frontier

logger = logging.getLogger(__name__)


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
        cascade: PageCascade | None = None,
        max_pages: int | None = None,
        max_attempts: int | None = None,
        max_seconds: float | None = None,
        max_seconds_slow: float | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter or DomainLimiter()
        # Каскад собирает тот, кто ведёт обход: задача очереди — с платными уровнями
        # и журналом расхода, консольный замер — со своим браузером, без них.
        self._cascade = cascade or PageCascade(client)
        self._health = CrawlHealth()
        self._max_pages = max_pages if max_pages is not None else cfg.MAX_PAGES_PER_DONOR
        self._max_attempts = (
            max_attempts if max_attempts is not None else cfg.MAX_ATTEMPTS_PER_DONOR
        )
        self._max_seconds = max_seconds if max_seconds is not None else cfg.MAX_SECONDS_PER_DONOR
        self._slow_cap = max_seconds_slow if max_seconds_slow is not None else cfg.MAX_SECONDS_SLOW
        # Срок донора по паузе сайта (`pace.py`); пусто — ещё не посчитан.
        self._budget: float | None = None
        self._clock = ResponseClock()
        self._attempts = 0
        self._slowed = False
        self.articles = 0
        self.links: list[OutLink] = []
        self.share_links = 0
        self.pages_labeled = 0
        self.pages_dated = 0
        # Сколько свежих адресов дала лента, когда карты нет (`feeds.py`) — отчёту.
        self._feed_items = 0
        self._progress: Progress | None = None
        self._sent = 0
        self._started = time.monotonic()
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
        started = self._started = time.monotonic() - point.elapsed_sec
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

        pause = self._limiter.set_delay(host, rules.crawl_delay)
        self._budget = point.time_budget_sec or self._time_budget(host, pause)
        # Срок общий на всего донора, а не на обход страниц: robots и карты
        # тоже запросы, и на сайте с длинной паузой они одни съедают минуты.
        found = await read_sources(
            self._client,
            self._limiter,
            self._cascade.headers,
            host=host,
            home=home,
            rules=rules,
            point=point,
            deadline=started + self._budget,
        )
        self._feed_items = len(found.fresh)
        frontier, opened = start_frontier(home, host, found, point)
        pages, stop = await self._walk(host, rules, frontier, started, opened=opened)
        await self._send(pages, frontier)
        return self._report(
            host,
            self._outcome(pages, stop),
            stop,
            rules,
            started,
            pages=pages,
            scan=found.scan,
            frontier=frontier,
        )

    def _time_budget(self, host: str, pause: float) -> float:
        """Срок донора по паузе сайта и времени его ответа (`pace.py`) — раз, до карты."""
        budget = round(
            time_budget(
                pause=pause,
                response=self._clock.average,
                pages=self._max_pages,
                base=self._max_seconds,
                ceiling=self._slow_cap,
            )
        )
        if budget > self._max_seconds:
            logger.info(
                "обход %s: пауза %.1f с, ответ %.2f с — срок %s с вместо %.0f",
                host,
                pause,
                self._clock.average,
                budget,
                self._max_seconds,
            )
        return budget

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
        """robots.txt в первом виде хоста, который ответил.

        Заголовки — те же, что у страниц (`PageCascade.headers`). До 20.10.2026 robots.txt
        запрашивался голым клиентом с именем `python-httpx`. Сайт, который режет это имя,
        отвечал на robots.txt отказом, и донор не обходился вовсе: «не прочитан» — значит
        «не согласие». К тому же запрет читался для одного имени, а страницы просило другое.
        """
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}/robots.txt"
            async with self._limiter.slot(host):
                began = time.monotonic()
                try:
                    response = await self._client.get(
                        url,
                        headers=self._cascade.headers,
                        follow_redirects=True,
                        timeout=cfg.ROBOTS_TIMEOUT_SEC,
                    )
                except httpx.HTTPError as exc:
                    logger.info("robots: %s не открылся (%r)", url, exc)
                    continue
                # Ответ — любой: время ответа сайта идёт в срок донора (`pace.py`).
                self._clock.record(time.monotonic() - began)
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
                began = time.monotonic()
                result = await self._cascade.get(url)
            self._attempts += 1
            self._health.record(result.outcome)
            if result.outcome is FetchOutcome.OK:
                # Время отказа — не время ответа: срок по нему не считается.
                self._clock.record(time.monotonic() - began)
                return result
        return None

    async def _walk(
        self,
        host: str,
        rules: RobotsRules,
        frontier: Frontier,
        started: float,
        *,
        opened: list[str],
    ) -> tuple[list[str], StopReason]:
        """Собственно обход. Возвращает открытые страницы и причину конца:
        «адреса кончились» — когда кончились и карта, и ссылки."""
        pages = list(opened)
        # Уже открытое не открывается второй раз. Карта сайта почти всегда
        # перечисляет главную, а её качает проверка «сайт открывается»:
        # без этой проверки донор получает лишний запрос, а отчёт —
        # лишнюю статью, которой не было.
        visited: set[str] = set(opened)

        while frontier:
            stop = self._limit_hit(host, pages, started)
            if stop is not None:
                return pages, stop
            await self._stop_if_asked(pages, frontier)

            url, by_link = frontier.pop()
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
            frontier.opened(by_link=by_link)
            self._harvest(result.html, result.url, host)
            self._follow(result, host, frontier)
            await self._send_if_due(pages, frontier)

        return pages, StopReason.EXHAUSTED

    async def _send_if_due(self, pages: list[str], frontier: Frontier) -> None:
        """Пачка по ходу — если обход ведёт задача очереди и пора."""
        if self._progress is not None and self._progress.due(len(pages)):
            await self._send(pages, frontier)

    async def _stop_if_asked(self, pages: list[str], frontier: Frontier) -> None:
        """Просьба остановиться (выкатка) — на границе страницы: пачка
        дописывается, и обход выходит с чекпоинтом, а не обрывается."""
        if self._progress is None or not self._progress.stop():
            return
        raise CrawlInterruptedError(await self._send(pages, frontier))

    async def _send(self, pages: list[str], frontier: Frontier) -> Checkpoint:
        """Ссылки с прошлой пачки и чекпоинт — тому, кто ведёт обход."""
        # Очередь ссылок — не длиннее оставшихся попыток: больше не открыть.
        left = max(self._max_attempts - self._attempts, 0)
        checkpoint = Checkpoint(
            source=frontier.source,
            pages=list(pages),
            queue=frontier.saved(left),
            attempts=self._attempts,
            articles=self.articles,
            share_links=self.share_links,
            pages_labeled=self.pages_labeled,
            pages_dated=self.pages_dated,
            elapsed_sec=round(time.monotonic() - self._started, 1),
            slowed=self._slowed or self._was_slowed,
            from_map=frontier.from_map,
            from_links=frontier.from_links,
            time_budget_sec=self._budget or 0.0,
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

    def _follow(self, result: FetchResult, host: str, frontier: Frontier) -> None:
        """Ссылки со страницы — в очередь, и с картой тоже: после карты обход идёт по ним."""
        if result.html is not None:
            frontier.add(same_site_links(result.html, result.url, host))

    def _limit_hit(self, host: str, pages: list[str], started: float) -> StopReason | None:
        """Какой потолок кончился первым. `None` — можно продолжать."""
        if len(pages) >= self._max_pages:
            return StopReason.MAX_PAGES
        if self._attempts >= self._max_attempts:
            return StopReason.MAX_ATTEMPTS
        # Срок не меньше общего (`pace.time_budget`), так что до расчёта — общий. Вышел —
        # продлевается по темпу (`pace.extended`): чекпоинт и отчёт видят продлённый.
        elapsed, budget = time.monotonic() - started, self._budget or self._max_seconds
        if elapsed >= budget:
            longer = extended(
                host,
                budget=budget,
                elapsed=elapsed,
                opened=len(pages),
                pages=self._max_pages,
                ceiling=self._slow_cap,
            )
            if longer is None:
                return StopReason.TIMEOUT
            self._budget = longer
        if self._health.verdict is HealthVerdict.STOP:
            return StopReason.UNHEALTHY
        return None

    def _react(self, host: str) -> None:
        """Ответ на просевшее здоровье: вдвое медленнее, и один раз."""
        if self._slowed or self._health.verdict is not HealthVerdict.SLOW_DOWN:
            return
        delay = self._limiter.slow_down(host)
        self._cascade.rotate_proxy(f"отказов {self._health.failure_share:.0%} в окне здоровья")
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
        frontier: Frontier | None = None,
    ) -> CrawlReport:
        found, complete = (scan.found, scan.complete) if scan else (False, False)
        # `is not None`: пустая очередь — ложь (`Frontier.__bool__`), а она у кончившегося обхода.
        source, from_map, from_links = frontier.facts() if frontier is not None else ("none", 0, 0)
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
            sitemap_found=found,
            sitemap_complete=complete,
            source=source,
            pages_from_sitemap=from_map,
            pages_from_links=from_links,
            feed_items=self._feed_items,
            time_budget_sec=self._budget,
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
