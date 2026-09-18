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
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from backend.config import contacts as cfg
from backend.features.contacts.extract import extract_emails, find_contact_links
from backend.features.contacts.pages import HEADERS, LINK_MARKERS, SLUGS, WALK_ORDER

logger = logging.getLogger(__name__)

#: Сколько страниц открывает браузер. Меньше, чем у обычного обхода:
#: каждая стоит секунд, а не долей секунды.
MAX_BROWSER_PAGES = 3


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
        self._playwright: object | None = None
        self._browser: object | None = None

    async def __aenter__(self) -> PlaywrightRenderer | None:
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415 — пакет
            # необязательный: импорт наверху файла сделал бы его обязательным
            # для всего проекта, включая прогоны, где браузер не нужен.
        except ImportError:
            logger.warning(
                "контакты: playwright не установлен — ступень браузера пропускается. "
                "Поставить: pip install -e '.[browser]' и playwright install chromium"
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
                await (resource.close() if name == "браузер" else resource.stop())  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 — закрытие не должно ронять прогон
                logger.debug("контакты: %s не закрылся: %r", name, exc)
        self._browser = None
        self._playwright = None

    async def render(self, url: str) -> str | None:
        if self._browser is None:
            return None

        context = None
        try:
            context = await self._browser.new_context(  # type: ignore[attr-defined]
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                ignore_https_errors=True,
            )
            page = await context.new_page()
            await page.goto(
                url, timeout=int(cfg.BROWSER_TIMEOUT_SEC * 1000), wait_until="domcontentloaded"
            )
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


def browser_urls(site_host: str) -> list[str]:
    """Что открывать браузером: главная и две самые вероятные страницы.

    Список короткий намеренно — каждая страница стоит секунд. Угадывать
    здесь почти нечего: если сайт закрыт, то закрыт целиком, а если дело
    в JavaScript, адрес обычно в футере главной.
    """
    root = f"https://{site_host}"
    first = [SLUGS[kind][0] for kind in WALK_ORDER[:2]]  # деньги и контакты
    return [f"{root}/", *(f"{root}/{slug}/" for slug in first)]


async def find_emails(renderer: PageRenderer, site_host: str) -> dict[str, str]:
    """Адреса, которые видно только браузеру. Ключ — адрес, значение — где нашли.

    Пустой словарь законен: сайт мог не открыться и в браузере.
    """
    found: dict[str, str] = {}
    queue = browser_urls(site_host)
    seen: set[str] = set()

    for url in queue[:MAX_BROWSER_PAGES]:
        if url in seen:
            continue
        seen.add(url)

        html = await renderer.render(url)
        if not html:
            continue

        for email in extract_emails(html):
            found.setdefault(email, url)

        if not found and url.endswith("/"):
            # Со страницы, которая открылась, берём ссылки на контактные
            # разделы: угадывание слагов здесь слишком дорого.
            for href in list(find_contact_links(html, slugs=LINK_MARKERS))[:2]:
                absolute = (
                    href if href.startswith("http") else f"https://{site_host}/{href.lstrip('/')}"
                )
                if absolute not in seen and len(seen) < MAX_BROWSER_PAGES:
                    queue.append(absolute)

    return found
