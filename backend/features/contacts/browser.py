"""Ступень 1б: рендер страницы настоящим браузером.

Отдельная ступень, а не улучшение обхода, потому что цена у неё другая.
Обычный запрос — это доли секунды и мегабайты; браузер — это секунды
на страницу и сотни мегабайт зависимости. Ставить его в общий поток
значит замедлить весь прогон ради каждого шестого домена.

**Зачем он вообще нужен.** Семь сайтов из 44 не открываются никакими
заголовками: отдают 403 или рисуют футер с адресом на JavaScript.
Обычный запрос по ним получает либо отказ, либо пустой каркас страницы.

**Когда ступень включается.** Только если обычный обход ничего не дал
и при этом сайт либо закрылся, либо отдал пустые страницы. Домен,
с которого адрес уже снят, браузер не видит.

**Отказ браузера — не отказ домена.** Не установлен, не запустился,
упал на середине — всё это даёт пустой результат и запись в лог,
а лестница идёт дальше на платную ступень. Иначе отсутствие
необязательной зависимости роняло бы прогон целиком.

**Страница отказа — не страница сайта.** Ответ 4xx/5xx браузер тоже
рисует, но рисует он проверку «вы не робот» со своей формой или
заглушку хостера с его адресом. Принять такое за сайт значило бы
записать в контакты чужое.

**Сеть браузера — только через свой прокси** (`browser_proxy.py`, ревью 20.10.2026): он
проверяет каждое соединение — страницы, воркеров, сокетов, preconnect — тем же правилом,
что обычный обход, и соединяется с проверенным адресом; UDP мимо прокси выключен. Перехват
маршрутов вкладки — второй слой.

**Защита исходящих — та же, что у обычного обхода** (`url_guard`, ревью 20.10.2026).
Каждый запрос вкладки — страница, её скрипты и запросы скриптов, шаг редиректа — идёт
через проверку адреса (`_guarded`): публичный сайт иначе увёл бы браузер внутрь сети
одним скриптом или 302 на `169.254.169.254`. **Редирект проходит перехват, а не браузер:**
шаг цепочки, по которому браузер пошёл бы сам, перехват не видит (замер настоящим
Chromium 20.10.2026: 302 на «внутренний» адрес уходил мимо проверки и при ответе
вкладке как есть). Поэтому цепочка идёт здесь, шаг за шагом, каждый — через проверку,
а вкладка получает ответ последнего шага. Сервис-воркеры выключены — их запросы шли бы
мимо перехвата; веб-сокеты закрываются; картинки, шрифты и видео не грузятся — адреса в
них нет. Отрисованная страница — в тех же пределах разбора, что скачанная
(`pages.bounded`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin

from backend.config import contacts as cfg
from backend.features.contacts.browser_proxy import CHROMIUM_ARGS, BrowserProxy, Resolve, checked
from backend.features.contacts.extract import find_contact_links
from backend.features.contacts.pages import HEADERS, FetchedPage, bounded, is_challenge
from backend.features.contacts.slugs import LINK_MARKERS, SLUGS, WALK_ORDER
from backend.features.core.domain import PageKind
from backend.shared.net.url_guard import UnsafeUrlError, assert_safe_url

logger = logging.getLogger(__name__)

#: Сколько страниц открывает браузер. Меньше, чем у обычного обхода:
#: каждая стоит секунд, а не долей секунды.
MAX_BROWSER_PAGES = 3

#: Что вкладка не грузит вовсе: адреса в этом нет, а запросов и времени — больше всего.
SKIPPED_RESOURCES = frozenset({"image", "media", "font"})


#: Шагов редиректа на один запрос вкладки: дальше — отказ, как у бесконечной цепочки.
MAX_HOPS = 10
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


async def _hop(route: Any, url: str) -> Any | None:
    """Один шаг запроса — через проверку адреса и без перехода по редиректу. `None` —
    дальше нельзя: адрес не прошёл защиту или сайт не ответил."""
    try:
        await asyncio.to_thread(assert_safe_url, url)
    except UnsafeUrlError as exc:
        logger.info("контакты: браузер не пошёл по адресу — %s", exc)
        return None
    try:
        return await route.fetch(url=url, max_redirects=0)
    except Exception as exc:  # noqa: BLE001 — чужой сайт; шаг на другую схему Playwright не делает
        logger.debug("контакты: браузер не получил %s: %r", url, exc)
        return None


async def _guarded(route: Any) -> None:
    """Запрос вкладки — через защиту исходящих (`url_guard.assert_safe_url`), как у
    обычного обхода: адрес проверяется на каждом запросе и на каждом шаге редиректа.

    Цепочка редиректа идёт здесь (`_hop`), а вкладка получает ответ последнего шага:
    по редиректу, который браузер проходит сам, перехват не зовётся (см. модуль).
    """
    request = route.request
    if request.resource_type in SKIPPED_RESOURCES:
        await route.abort()
        return
    url = request.url
    for _ in range(MAX_HOPS):
        response = await _hop(route, url)
        if response is None:
            await route.abort("blockedbyclient")
            return
        location = response.headers.get("location")
        if response.status not in _REDIRECTS or not location:
            await route.fulfill(response=response)
            return
        url = urljoin(url, location)
    logger.debug("контакты: у %s больше %d шагов редиректа", request.url, MAX_HOPS)
    await route.abort()


async def _closed(socket: Any) -> None:
    """Веб-сокет вкладки — закрыть, не соединяя: адреса в нём нет, а перехват страниц
    (`_guarded`) сокеты не видит."""
    await socket.close()


async def _guard(context: Any) -> None:
    """Защита исходящих — на всю вкладку, до первого запроса."""
    await context.route("**/*", _guarded)
    # Перехват сокетов — с Playwright 1.48 (пакет — `>=1.47`): на старом сокеты остаются
    # без проверки, и это сказано в журнале, а не молчанием.
    route_sockets = getattr(context, "route_web_socket", None)
    if route_sockets is None:
        logger.warning("контакты: Playwright без перехвата веб-сокетов — обновить до 1.48+")
        return
    await route_sockets("**/*", _closed)


#: Ссылки страницы → адреса своего домена с видом раздела. Это
#: `PageFetcher.follow`: правило «только свой домен, только http(s)»
#: одно на обычный обход и на браузер.
Follow = Callable[[FetchedPage, set[str]], list[tuple[str, PageKind]]]


@runtime_checkable
class PageRenderer(Protocol):
    """Умеет отдать HTML страницы так, как его видит браузер."""

    async def render(self, url: str) -> str | None:
        """HTML после исполнения скриптов. `None` — открыть не удалось."""
        ...


def launch_options(headless: bool, proxy_url: str) -> dict[str, Any]:
    """Как запускать Chromium: сеть — только через свой прокси, петлевые адреса тоже, UDP
    мимо прокси — нет (`browser_proxy.CHROMIUM_ARGS`)."""
    return {"headless": headless, "proxy": {"server": proxy_url}, "args": list(CHROMIUM_ARGS)}


class PlaywrightRenderer:
    """Хромиум через Playwright. Пакет необязательный: нет — ступени нет.

    Браузер поднимается один раз на прогон, а не на домен: запуск стоит
    около секунды, и на сотне доменов это лишние полторы минуты.
    """

    def __init__(self, *, headless: bool = True, resolve: Resolve = checked) -> None:
        self._headless = headless
        # Сеть браузера — только через свой прокси (`browser_proxy.py`): перехват маршрутов
        # не видит запросов воркеров, WebRTC и preconnect. `resolve` — проверка цели.
        self._proxy = BrowserProxy(resolve)
        self._proxy_url = ""
        # Тип намеренно `Any`: пакет необязательный, и там, где его нет —
        # в CI, например, — типизатор о нём ничего не знает. Указав здесь
        # что-то конкретнее, мы получили бы проверку, которая зелёная
        # на машине разработчика и красная на сборке.
        self._playwright: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> PlaywrightRenderer | None:
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415 — пакет
            # необязательный: импорт наверху файла сделал бы его обязательным
            # для всего проекта, включая прогоны, где браузер не нужен.
        except ImportError:
            logger.warning(
                "контакты: playwright не установлен — ступень браузера пропускается. "
                "Поставить: uv sync --extra browser и playwright install chromium"
            )
            return None

        try:
            self._proxy_url = await self._proxy.start()
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                **launch_options(self._headless, self._proxy_url)
            )
        except Exception as exc:  # noqa: BLE001 — браузер необязателен, падать нельзя
            logger.warning("контакты: браузер не запустился (%r) — ступень пропускается", exc)
            await self.aclose()
            return None
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        for name, resource in (("браузер", self._browser), ("playwright", self._playwright)):
            if resource is None:
                continue
            try:
                await (resource.close() if name == "браузер" else resource.stop())
            except Exception as exc:  # noqa: BLE001 — закрытие не должно ронять прогон
                logger.debug("контакты: %s не закрылся: %r", name, exc)
        self._browser = None
        self._playwright = None
        await self._proxy.close()

    async def render(self, url: str) -> str | None:
        if self._browser is None:
            return None

        context = None
        try:
            context = await self._browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                ignore_https_errors=True,
                # Запросы сервис-воркера идут мимо перехвата (`_guarded`).
                service_workers="block",
                # И у вкладки — тот же прокси: запросы перехвата (`route.fetch`) делает
                # Playwright, а не браузер, и они тоже идут через проверку сети.
                proxy={"server": self._proxy_url},
            )
            await _guard(context)
            page = await context.new_page()
            answer = await page.goto(
                url, timeout=int(cfg.BROWSER_TIMEOUT_SEC * 1000), wait_until="domcontentloaded"
            )
            if answer is not None and answer.status >= 400:
                logger.debug(
                    "контакты: браузер получил %s на %s — не страница сайта", answer.status, url
                )
                return None
            # Футер с адресом часто дорисовывается после загрузки.
            await page.wait_for_timeout(int(cfg.BROWSER_SETTLE_SEC * 1000))
            return bounded(str(await page.content()))
        except Exception as exc:  # noqa: BLE001 — чужая страница, отказ рутинный
            logger.debug("контакты: браузер не открыл %s: %r", url, exc)
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("контакты: вкладка не закрылась: %r", exc)


def _likely_sections(site_host: str) -> list[tuple[str, PageKind]]:
    """Два самых вероятных раздела — деньги и контакты, первым слагом вида."""
    return [(f"https://{site_host}/{SLUGS[kind][0]}/", kind) for kind in WALK_ORDER[:2]]


def browser_urls(site_host: str) -> list[str]:
    """Что открывать браузером, если ссылок на главной нет: она и два раздела.

    Список короткий намеренно — каждая страница стоит секунд. Угадывать
    здесь почти нечего: если сайт закрыт, то закрыт целиком, а если дело
    в JavaScript, адрес обычно в футере главной.
    """
    return [f"https://{site_host}/", *(url for url, _ in _likely_sections(site_host))]


async def render_pages(
    renderer: PageRenderer, site_host: str, *, follow: Follow, max_pages: int = MAX_BROWSER_PAGES
) -> list[FetchedPage]:
    """Страницы сайта глазами браузера: главная, разделы по её ссылкам, затем догадки.

    Отдаются страницы целиком, а не адреса с них: разбирает их тот же
    `site_steps.harvest`, что и скачанные, — с каналами связи и отметкой о форме.
    Пустой список законен: сайт мог не открыться и в браузере.

    Ссылки главной идут раньше догадок: раздел там назван словами и лежит
    по любому адресу, хоть `/p/42`. До 30.09.2026 они не открывались вовсе —
    цикл шёл по срезу очереди, снятому до того, как ссылки в неё дописывались.

    `max_pages` — потолок страниц: у обычного прохода три, у поиска глубже — шире
    (`CONTACTS_DEEP_BROWSER_PAGES`).
    """
    home_url = f"https://{site_host}/"
    pages: list[FetchedPage] = []
    queue: list[tuple[str, PageKind]] = []

    # Страница проверки «вы не робот», которую браузер не прошёл, — не страница сайта:
    # разобрав её, лестница записала бы сайт прочитанным, а «не пустил» потерялось бы.
    html = await renderer.render(home_url)
    if html and not is_challenge(html):
        home = FetchedPage(url=home_url, kind=PageKind.HOME, html=html)
        pages.append(home)
        queue = follow(home, find_contact_links(html, slugs=LINK_MARKERS))

    opened = {home_url}
    for url, kind in [*queue, *_likely_sections(site_host)]:
        if len(opened) >= max_pages:
            break
        if url in opened:
            continue
        opened.add(url)
        html = await renderer.render(url)
        if html and not is_challenge(html):
            pages.append(FetchedPage(url=url, kind=kind, html=html))
    return pages
