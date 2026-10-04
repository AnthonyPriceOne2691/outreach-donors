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
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from backend.config import contacts as cfg
from backend.features.contacts.extract import find_contact_links
from backend.features.contacts.pages import HEADERS, FetchedPage
from backend.features.contacts.slugs import LINK_MARKERS, SLUGS, WALK_ORDER
from backend.features.core.domain import PageKind

logger = logging.getLogger(__name__)

#: Сколько страниц открывает браузер. Меньше, чем у обычного обхода:
#: каждая стоит секунд, а не долей секунды.
MAX_BROWSER_PAGES = 3

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


class PlaywrightRenderer:
    """Хромиум через Playwright. Пакет необязательный: нет — ступени нет.

    Браузер поднимается один раз на прогон, а не на домен: запуск стоит
    около секунды, и на сотне доменов это лишние полторы минуты.
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
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
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
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

    async def render(self, url: str) -> str | None:
        if self._browser is None:
            return None

        context = None
        try:
            context = await self._browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                ignore_https_errors=True,
            )
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
            return str(await page.content())
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
    renderer: PageRenderer, site_host: str, *, follow: Follow
) -> list[FetchedPage]:
    """Страницы сайта глазами браузера: главная, разделы по её ссылкам, затем догадки.

    Отдаются страницы целиком, а не адреса с них: разбирает их тот же
    `_harvest`, что и скачанные, — с каналами связи и отметкой о форме.
    Пустой список законен: сайт мог не открыться и в браузере.

    Ссылки главной идут раньше догадок: раздел там назван словами и лежит
    по любому адресу, хоть `/p/42`. До 30.09.2026 они не открывались вовсе —
    цикл шёл по срезу очереди, снятому до того, как ссылки в неё дописывались.
    """
    home_url = f"https://{site_host}/"
    pages: list[FetchedPage] = []
    queue: list[tuple[str, PageKind]] = []

    html = await renderer.render(home_url)
    if html:
        home = FetchedPage(url=home_url, kind=PageKind.HOME, html=html)
        pages.append(home)
        queue = follow(home, find_contact_links(html, slugs=LINK_MARKERS))

    opened = {home_url}
    for url, kind in [*queue, *_likely_sections(site_host)]:
        if len(opened) >= MAX_BROWSER_PAGES:
            break
        if url in opened:
            continue
        opened.add(url)
        if html := await renderer.render(url):
            pages.append(FetchedPage(url=url, kind=kind, html=html))
    return pages
