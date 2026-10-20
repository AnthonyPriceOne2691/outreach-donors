"""Главные примеров: открыть, как открывает обход, и снять то, что сайт говорит о себе.

**Путь — тот же, что у обхода доноров** (`crawl/walk.py`): robots.txt до первого
запроса к странице — запрет главной значит «не открываем», а непрочитанный файл
значит то же: молчание сайта не согласие (`crawl/robots.py`); одна операция на хост
и пауза между ними, `Crawl-delay` сайта сильнее нашей паузы (`crawl/limiter.py`);
защита исходящих и потолок байт (`shared/net/url_guard.py`); первый уровень каскада
(`crawl/fetch.py`) — без браузера, прокси и сервиса рендеринга: пример стоит модели,
а не денег за обход.

**Отказ — по каждому сайту и словами.** «Не открылся» без причины не даёт поправить
пример; причина едет на экран рядом с адресом. Из нескольких видов адреса главной
(`https`/`http`, с `www.` и без) называется самый говорящий отказ: «закрылся от нас»
полезнее «не ответил по http».

**Снимается то, что сайт говорит о себе**: заголовок, описание, заголовки разделов,
меню и отрывок видимого текста — с потолками: десять главных целиком утопили бы
вызов о теме в подвалах сайтов. Главная без текста (скелет, который рисует скрипт)
и заглушка защиты вместо главной — отказы, а не «текст сайта».
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from selectolax.parser import HTMLParser

from backend.config import crawl as crawl_cfg
from backend.features.contacts.pages import home_variants
from backend.features.crawl import robots as robots_rules
from backend.features.crawl.article import strip_structural_noise
from backend.features.crawl.fetch import FetchOutcome, FetchResult, PageCascade, headers_for
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.proxies import ProxyPool
from backend.features.crawl.robots import RobotsRules
from backend.features.donors.home_signals import looks_denied, read_home
from backend.features.donors.host import normalize_host
from backend.features.keywords.examples import MAX_EXAMPLES, Example
from backend.features.runs.exclusions import ExclusionReason, by_name
from backend.shared.net.url_guard import UnsafeUrlError

logger = logging.getLogger(__name__)

#: Сколько главных открываются одновременно — все примеры разом: хосты у них разные,
#: и вежливость держит ограничитель каждого хоста, а не очередь между ними. По очереди
#: десять медленных сайтов ждали бы десять потолков времени вместо одного.
SITES_AT_ONCE = MAX_EXAMPLES
#: Сколько ждём один сайт целиком: robots.txt и все виды адреса главной.
SITE_SECONDS = 30.0
#: Отрывок видимого текста главной, знаков. Тему называют заголовки и описание;
#: текст — подкрепление, и на десяти сайтах он главный счёт токенов вызова.
TEXT_CHARS = 1000
#: Заголовков разделов с главной и знаков в каждом.
HEADINGS = 12
HEADING_CHARS = 120
#: Пунктов меню. Меню называет разделы сайта — по ним видна ниша.
MENU_ITEMS = 15
#: Меньше стольких знаков текста (заголовок, описание, заголовки разделов, отрывок)
#: — тему по главной не понять: так выглядит скелет, который рисует скрипт.
MIN_SIGNAL_CHARS = 80

#: Управляющие знаки вне пробелов: в отрывке, который уходит модели, им не место.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Насколько говорящ отказ: из нескольких видов адреса называется самый говорящий.
_TELLING = {FetchOutcome.BLOCKED: 0, FetchOutcome.MISSING: 1, FetchOutcome.ERROR: 2}
#: Коды, которыми сайт закрывается от нас (`crawl/fetch.classify`); прочий отказ
#: `BLOCKED` — заставка защиты в теле страницы с кодом 200.
_CLOSED = frozenset({401, 403, 429})

#: Сетевые сбои словами — по имени исключения httpx (`FetchResult.reason` начинается
#: с него). Имя, которого здесь нет, называется как есть: тоже причина.
_NETWORK_WORDS = {
    "ConnectTimeout": "не ответил вовремя",
    "ReadTimeout": "не ответил вовремя",
    "WriteTimeout": "не ответил вовремя",
    "PoolTimeout": "не ответил вовремя",
    "ConnectError": "не открылся: имя не найдено или соединение отклонено",
}


@dataclass(frozen=True, slots=True)
class ExamplePage:
    """Главная одного примера. `reason` — почему не годится; пусто — прочитана."""

    example: Example
    reason: str = ""
    url: str = ""
    title: str = ""
    description: str = ""
    headings: tuple[str, ...] = ()
    menu: tuple[str, ...] = ()
    text: str = ""
    language: str = ""

    @property
    def read(self) -> bool:
        return not self.reason


def _clean(text: str) -> str:
    """Строка без управляющих знаков и лишних пробелов."""
    return " ".join(_CONTROL.sub(" ", text).split())


def _cut(text: str, limit: int) -> str:
    """Не длиннее `limit` знаков, по границе слова, если она есть недалеко."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return head[:space] if space > limit // 2 else head


def platform_refusal(host: str) -> str:
    """Пример — платформа или соцсеть: её главная говорит о самой площадке, а не
    о нише. Правило то же, что у прогона (`runs/exclusions.by_name`), — по самому
    имени и по корневому домену (`m.соцсеть` — та же соцсеть); пусто — годен."""
    names = [name for name in dict.fromkeys((host, normalize_host(host))) if name]
    found = by_name(names)
    if any(found.get(name) is ExclusionReason.PLATFORM for name in names):
        return (
            "платформа или соцсеть: её главная — о самой площадке, а не о теме ниши. "
            "Дайте сайт, похожий на те, что ищете"
        )
    return ""


def network_words(reason: str) -> str:
    """Сетевой сбой словами по имени исключения в начале причины."""
    name = reason.split(":", 1)[0].strip()
    return _NETWORK_WORDS.get(name, f"не открылся ({name or 'обрыв'})")


def refusal_words(result: FetchResult) -> str:
    """Почему главная не годится — словами по исходу каскада."""
    reason = result.reason or ""
    if result.outcome is FetchOutcome.BLOCKED:
        if result.status in _CLOSED:
            return f"сайт закрылся от нас (код {result.status})"
        return "вместо главной — заставка защиты от ботов"
    if result.outcome is FetchOutcome.MISSING:
        if reason == "не HTML":
            return "по адресу главной не страница, а другой файл (не HTML)"
        return f"главной нет (код {result.status})"
    if result.status is not None:
        return f"сайт ответил ошибкой (код {result.status})"
    return network_words(reason)


def _most_telling(results: Sequence[FetchResult]) -> FetchResult:
    return min(results, key=lambda result: _TELLING.get(result.outcome, len(_TELLING)))


def _headings(tree: HTMLParser) -> tuple[str, ...]:
    found: list[str] = []
    for node in tree.css("h1, h2, h3"):
        text = _cut(_clean(node.text(separator=" ")), HEADING_CHARS)
        if len(text) >= 3 and text not in found:
            found.append(text)
        if len(found) >= HEADINGS:
            break
    return tuple(found)


def _meta(tree: HTMLParser, selector: str) -> str:
    node = tree.css_first(selector)
    return _clean((node.attributes.get("content") or "") if node else "")


def _language(tree: HTMLParser) -> str:
    node = tree.css_first("html")
    return _clean(node.attributes.get("lang") or "")[:16] if node else ""


def _visible_text(tree: HTMLParser) -> str:
    """Отрывок того, что видно на странице, без кода, меню, шапки и подвала —
    тем же отсевом, что у проверки «страница пустая» (`crawl/render.looks_empty`)."""
    strip_structural_noise(tree)
    body = tree.body
    return _cut(_clean(body.text(separator=" ") if body is not None else ""), TEXT_CHARS)


def page_of(example: Example, url: str, html: str) -> ExamplePage:
    """Разбор готовой главной. Без сети — тесты гоняют его на образцах."""
    signals = read_home(html)
    title, description = _clean(signals.title), _clean(signals.description)
    if looks_denied(f"{title}\n{description}"):
        return ExamplePage(
            example, reason=f"вместо главной — заглушка защиты: «{_cut(title, 60)}»", url=url
        )
    tree = HTMLParser(html)
    description = description or _cut(_meta(tree, 'meta[property="og:description"]'), 400)
    headings = _headings(tree)
    language = _language(tree)
    text = _visible_text(tree)
    if len(title) + len(description) + sum(map(len, headings)) + len(text) < MIN_SIGNAL_CHARS:
        return ExamplePage(
            example,
            reason="на главной почти нет текста — его рисует скрипт или сайт пуст: "
            "тему по ней не понять",
            url=url,
        )
    menu = tuple(_clean(item) for item in signals.nav[:MENU_ITEMS])
    return ExamplePage(
        example,
        url=url,
        title=title,
        description=description,
        headings=headings,
        menu=menu,
        text=text,
        language=language,
    )


class PageReader:
    """Главные примеров — по одной на сайт, под ограничителем и потолком времени."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        limiter: DomainLimiter | None = None,
        site_seconds: float = SITE_SECONDS,
    ) -> None:
        self._client = client
        self._limiter = limiter or DomainLimiter()
        # Только первый уровень: браузер, прокси и сервис рендеринга — деньги и
        # секунды обхода, а не сборки ключей.
        self._cascade = PageCascade(
            client, browser_enabled=False, proxies=ProxyPool(()), render=None, spending=None
        )
        self._headers = headers_for(crawl_cfg.IDENTIFY)
        # Чтение robots.txt — своим потолком, как у обхода, а соединение — тем же
        # коротким, что у главной: мёртвый сайт молчит на соединении (`home_timeout`).
        self._robots_timeout = httpx.Timeout(
            crawl_cfg.ROBOTS_TIMEOUT_SEC, connect=client.timeout.connect
        )
        self._seconds = site_seconds

    async def read_all(self, examples: Sequence[Example]) -> list[ExamplePage]:
        """Главные всех примеров, в порядке примеров."""
        gate = asyncio.Semaphore(SITES_AT_ONCE)

        async def one(example: Example) -> ExamplePage:
            async with gate:
                return await self.read(example)

        return list(await asyncio.gather(*(one(example) for example in examples)))

    async def read(self, example: Example) -> ExamplePage:
        """Главная одного примера. Не бросает: отказ — страницей с причиной."""
        refusal = platform_refusal(example.host)
        if refusal:
            return ExamplePage(example, reason=refusal)
        try:
            async with asyncio.timeout(self._seconds):
                return await self._read(example)
        except TimeoutError:
            logger.info("примеры: %s не прочитан за %g с", example.host, self._seconds)
            return ExamplePage(example, reason=f"не ответил за {self._seconds:g} с")
        except UnsafeUrlError as exc:
            # Имя ведёт во внутреннюю сеть — или редирект увёл туда: защита права.
            logger.warning("примеры: %s — адрес не прошёл защиту: %s", example.host, exc)
            return ExamplePage(example, reason="адрес ведёт во внутреннюю сеть — туда не ходим")

    async def _read(self, example: Example) -> ExamplePage:
        host = example.host
        rules, why, answered = await self._robots(host)
        if rules is None and not answered:
            # Сайт не ответил вовсе — это и есть причина, а не robots.txt.
            return ExamplePage(example, reason=why)
        if rules is None:
            return ExamplePage(
                example,
                reason=f"robots.txt не прочитан ({why}) — молчание сайта не согласие: "
                "главную не открываем",
            )
        self._limiter.set_delay(host, rules.crawl_delay)
        failures: list[FetchResult] = []
        for url in home_variants(host):
            if not rules.allows(url):
                return ExamplePage(example, reason="robots.txt запрещает главную — не открываем")
            async with self._limiter.slot(host):
                result = await self._cascade.get(url)
            if result.outcome is FetchOutcome.OK and result.html is not None:
                return page_of(example, result.url, result.html)
            failures.append(result)
        worst = _most_telling(failures)
        logger.info("примеры: главная %s не открылась — %s", host, worst.reason)
        return ExamplePage(example, reason=refusal_words(worst))

    async def _robots(self, host: str) -> tuple[RobotsRules | None, str, bool]:
        """robots.txt — как у обхода (`DonorCrawler._robots`): `https`, затем `http`;
        404 — ограничений нет; не прочитан ни там, ни там — `None` и почему.
        Третье — ответил ли сайт хоть кодом: не ответил — главная не откроется тоже."""
        why, answered = "не ответил", False
        for scheme in ("https", "http"):
            url = f"{scheme}://{host}/robots.txt"
            async with self._limiter.slot(host):
                try:
                    response = await self._client.get(
                        url,
                        headers=self._headers,
                        follow_redirects=True,
                        timeout=self._robots_timeout,
                    )
                except httpx.HTTPError as exc:
                    logger.info("примеры: robots %s не открылся (%r)", url, exc)
                    why = network_words(type(exc).__name__)
                    continue
            if response.status_code in (404, 410):
                return robots_rules.absent(), "", True
            if response.status_code >= 400:
                why, answered = f"код {response.status_code}", True
                continue
            text = response.text[: robots_rules.MAX_ROBOTS_BYTES]
            return robots_rules.parse_robots(text, crawl_cfg.USER_AGENT_TOKEN), "", True
        return None, why, answered
