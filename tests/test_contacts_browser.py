"""Ступень браузера: когда включается, что берёт, как падает.

Настоящий браузер здесь не поднимается. Он стоит секунд на страницу
и сотен мегабайт зависимости, а проверять надо не Chromium — его
проверяет сам Chromium, — а наши решения: кого он смотрит, кого нет
и что происходит, когда его нет вовсе.

Отдельной проверкой закрыт отказ: необязательная зависимость не должна
ронять прогон.
"""

from __future__ import annotations

import builtins

import httpx
import pytest
from backend.features.contacts import browser as browser_step
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.core.domain import ContactStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ступень 0 подменена: иначе тесты ходят в настоящий DNS и идут
    секундами вместо долей секунды."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


JS_PAGE = """
<html><body>
  <div id="app"></div>
  <footer><a href="mailto:ads@site.com">напишите нам</a></footer>
</body></html>
"""
EMPTY = "<html><body>адреса нет</body></html>"


class FakeRenderer:
    """Рендерер-заглушка: отдаёт заранее заданный HTML и считает открытия."""

    def __init__(self, pages: dict[str, str] | None = None, *, broken: bool = False) -> None:
        self.pages = pages or {}
        self.broken = broken
        self.opened: list[str] = []

    async def render(self, url: str) -> str | None:
        self.opened.append(url)
        if self.broken:
            return None
        return self.pages.get(url)


class ClosedSite:
    """Сайт, который закрылся от обычного запроса."""

    def __init__(self, status: int = 403) -> None:
        self.status = status
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(str(request.url))
        return httpx.Response(self.status, text="нет")


class OpenSite:
    """Сайт, который открывается и отдаёт адрес обычным запросом."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<html><body><a href="mailto:info@site.com">почта</a></body></html>',
            headers={"content-type": "text/html"},
        )


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


class TestWhenItRuns:
    async def test_browser_takes_a_site_that_closed_the_door(self) -> None:
        site = ClosedSite()
        renderer = FakeRenderer({"https://site.com/": JS_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http, renderer=renderer)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.FOUND
        assert result.contact is not None
        assert result.contact.email == "ads@site.com"
        assert ladder.counters.browser_found == 1

    async def test_browser_does_not_look_at_what_is_already_found(self) -> None:
        """Ступень дорогая: домен, с которого адрес снят, она не видит."""
        renderer = FakeRenderer({"https://site.com/": JS_PAGE})

        async with _client(OpenSite()) as http:
            ladder = ContactLadder(http, renderer=renderer)
            result = await ladder.find("site.com")

        assert result.contact is not None
        assert result.contact.email == "info@site.com"
        assert renderer.opened == []
        assert ladder.counters.browser_entered == 0

    async def test_without_a_renderer_the_step_is_absent(self) -> None:
        async with _client(ClosedSite()) as http:
            ladder = ContactLadder(http, renderer=None)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert ladder.counters.browser_entered == 0

    async def test_open_site_without_an_address_is_not_worth_rendering(self) -> None:
        """Сайт, который открылся и просто не показал адреса, рендером
        не исправить: замер на шести таких доменах дал ноль адресов,
        а стоил бы он полутора минут."""

        class OpenButEmpty:
            def __call__(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, text=EMPTY, headers={"content-type": "text/html"})

        renderer = FakeRenderer({"https://site.com/": JS_PAGE})

        async with _client(OpenButEmpty()) as http:
            ladder = ContactLadder(http, renderer=renderer)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert renderer.opened == []
        assert ladder.counters.browser_entered == 0

    async def test_page_budget_is_small(self) -> None:
        """Каждая страница стоит секунд — открываем единицы, а не десятки."""
        renderer = FakeRenderer({})

        async with _client(ClosedSite()) as http:
            await ContactLadder(http, renderer=renderer).find("site.com")

        assert len(renderer.opened) <= browser_step.MAX_BROWSER_PAGES


class TestWhenItFails:
    async def test_broken_browser_is_not_a_broken_domain(self) -> None:
        """Отказ необязательной ступени не должен ронять прогон: домен
        просто идёт дальше, на платную."""
        renderer = FakeRenderer(broken=True)

        async with _client(ClosedSite()) as http:
            ladder = ContactLadder(http, renderer=renderer)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert ladder.counters.browser_entered == 1
        assert ladder.counters.browser_found == 0

    async def test_missing_package_gives_no_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Playwright не установлен — ступени нет, и это сообщается, а не
        падает: пакет необязательный по замыслу."""
        real_import = builtins.__import__

        def refuse(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("playwright"):
                raise ImportError("нет пакета")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", refuse)

        async with browser_step.PlaywrightRenderer() as renderer:
            assert renderer is None


class TestUrls:
    async def test_short_list_of_the_most_likely_pages(self) -> None:
        urls = browser_step.browser_urls("site.com")
        assert urls[0] == "https://site.com/"
        assert len(urls) <= browser_step.MAX_BROWSER_PAGES
        assert all(url.startswith("https://site.com/") for url in urls)
