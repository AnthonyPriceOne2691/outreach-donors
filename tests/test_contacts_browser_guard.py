"""Браузер — с той же защитой исходящих, что обычный обход (ревью 20.10.2026).

До правки вкладка ходила куда угодно: публичный сайт уводил её на внутренний адрес
скриптом или одним 302 — `url_guard` стоял только у обычного клиента. Теперь каждый
запрос вкладки идёт через `_guarded`: адрес проверяется, цепочка редиректа проходится
здесь же, шаг за шагом и каждый — через проверку, а вкладке отдаётся ответ последнего.

Почему цепочка здесь, а не у браузера: по шагу, который браузер проходит сам, перехват
не зовётся — замер настоящим Chromium на локальном сервере 20.10.2026 (302 на «внутренний»
адрес уходил мимо проверки и при ответе вкладке как есть, и при `continue_`).

Настоящий браузер здесь не поднимается: вкладка, контекст и запросы — заглушки в той
части, которой пользуется `render`. Имена не распознаются: проверка адреса видит
внутренний адрес по самому адресу, а публичному имени заглушка отвечает публичным.
"""

from __future__ import annotations

import logging

import pytest
from backend.config import contacts as cfg
from backend.features.contacts import browser as browser_step
from backend.shared.net import url_guard

pytestmark = pytest.mark.asyncio

SITE = "https://site.example.test/"
METADATA = "http://169.254.169.254/latest/meta-data/"


@pytest.fixture(autouse=True)
def _names_resolve_to_a_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Распознавание имён — без сети: публичное имя ведёт на публичный адрес."""
    monkeypatch.setattr(url_guard, "_resolved_addresses", lambda _host: ["93.184.216.34"])


class _Request:
    def __init__(self, url: str, resource_type: str = "document") -> None:
        self.url, self.resource_type = url, resource_type


class _Answer:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.headers = {"location": location} if location else {}


class _Route:
    """Перехваченный запрос вкладки: ответы по адресу и что с запросом сделали."""

    def __init__(
        self,
        url: str,
        resource_type: str = "document",
        *,
        answers: dict[str, _Answer] | None = None,
    ) -> None:
        self.request = _Request(url, resource_type)
        self.answers = answers or {url: _Answer(200)}
        self.calls: list[tuple[str, object]] = []

    async def abort(self, reason: str = "failed") -> None:
        self.calls.append(("abort", reason))

    async def fetch(self, *, url: str, max_redirects: int = 20) -> _Answer:
        assert max_redirects == 0, "браузер прошёл бы цепочку мимо проверки"
        self.calls.append(("fetch", url))
        if url not in self.answers:
            raise RuntimeError(f"нет ответа: {url}")
        return self.answers[url]

    async def fulfill(self, *, response: _Answer) -> None:
        self.calls.append(("fulfill", response.status))


class TestEveryRequestIsChecked:
    async def test_internal_address_is_not_requested(self) -> None:
        route = _Route(METADATA, "xhr")

        await browser_step._guarded(route)

        assert route.calls == [("abort", "blockedbyclient")]

    async def test_public_page_is_handed_to_the_tab(self) -> None:
        route = _Route(SITE)

        await browser_step._guarded(route)

        assert route.calls == [("fetch", SITE), ("fulfill", 200)]

    async def test_redirect_chain_is_walked_here_step_by_step(self) -> None:
        """Каждый шаг — через проверку; вкладка получает ответ последнего."""
        answers = {
            SITE: _Answer(301, "https://www.site.example.test/"),
            "https://www.site.example.test/": _Answer(302, "/ru/"),
            "https://www.site.example.test/ru/": _Answer(200),
        }
        route = _Route(SITE, answers=answers)

        await browser_step._guarded(route)

        assert route.calls == [
            ("fetch", SITE),
            ("fetch", "https://www.site.example.test/"),
            ("fetch", "https://www.site.example.test/ru/"),
            ("fulfill", 200),
        ]

    async def test_redirect_to_an_internal_address_stops_at_its_step(self) -> None:
        """302 на метаданные облака: туда запрос не уходит, вкладке — отказ."""
        answers = {SITE: _Answer(302, "/go"), f"{SITE}go": _Answer(302, METADATA)}
        route = _Route(SITE, answers=answers)

        await browser_step._guarded(route)

        assert route.calls == [
            ("fetch", SITE),
            ("fetch", f"{SITE}go"),
            ("abort", "blockedbyclient"),
        ]

    async def test_endless_redirect_ends_in_a_refusal(self) -> None:
        route = _Route(SITE, answers={SITE: _Answer(302, SITE)})

        await browser_step._guarded(route)

        assert route.calls[-1] == ("abort", "failed")
        assert len(route.calls) == browser_step.MAX_HOPS + 1

    async def test_step_the_site_does_not_answer_is_a_refusal(self) -> None:
        route = _Route(SITE, answers={SITE: _Answer(302, "/gone")})

        await browser_step._guarded(route)

        assert route.calls[-1] == ("abort", "blockedbyclient")

    @pytest.mark.parametrize("kind", sorted(browser_step.SKIPPED_RESOURCES))
    async def test_pictures_fonts_and_video_are_not_loaded(self, kind: str) -> None:
        route = _Route(f"{SITE}logo", kind)

        await browser_step._guarded(route)

        assert route.calls == [("abort", "failed")]


class _Page:
    def __init__(self, html: str) -> None:
        self._html = html

    async def goto(self, _url: str, **_kwargs: object) -> object:
        return type("Answer", (), {"status": 200})()

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def content(self) -> str:
        return self._html


class _Context:
    def __init__(self, html: str, *, sockets: bool = True) -> None:
        self._page = _Page(html)
        self.routes: list[str] = []
        if sockets:
            self.route_web_socket = self._sockets

    async def route(self, pattern: str, handler: object) -> None:
        assert handler is browser_step._guarded
        self.routes.append(pattern)

    async def _sockets(self, pattern: str, handler: object) -> None:
        assert handler is browser_step._closed
        self.routes.append(f"сокеты {pattern}")

    async def new_page(self) -> _Page:
        assert self.routes, "вкладка открыта до защиты"
        return self._page

    async def close(self) -> None:
        return None


class _Unguardable:
    """Контекст без перехвата запросов: защиту не поставить."""

    async def new_page(self) -> _Page:
        return _Page("<html><body>editor@site.example.test</body></html>")

    async def close(self) -> None:
        return None


class _Chromium:
    def __init__(self, context: object) -> None:
        self.context = context
        self.options: dict[str, object] = {}

    async def new_context(self, **options: object) -> object:
        self.options = options
        return self.context


def _renderer(context: object) -> tuple[browser_step.PlaywrightRenderer, _Chromium]:
    renderer = browser_step.PlaywrightRenderer()
    chromium = _Chromium(context)
    renderer._browser = chromium
    return renderer, chromium


class TestTheTab:
    async def test_guard_is_set_before_the_page_opens(self) -> None:
        context = _Context("<html><body>ok</body></html>")
        renderer, chromium = _renderer(context)

        assert await renderer.render(SITE) == "<html><body>ok</body></html>"
        assert context.routes == ["**/*", "сокеты **/*"]
        # Запросы сервис-воркера шли бы мимо перехвата.
        assert chromium.options["service_workers"] == "block"
        # Запросы перехвата (`route.fetch`) делает Playwright — и они через прокси сети.
        assert set(chromium.options["proxy"]) == {"server"}

    async def test_chromium_goes_online_only_through_the_proxy(self) -> None:
        """Сеть браузера — только через свой прокси: петлевые адреса тоже, UDP мимо — нет."""
        options = browser_step.launch_options(True, "http://127.0.0.1:5000")

        assert options["proxy"] == {"server": "http://127.0.0.1:5000"}
        assert "--proxy-bypass-list=<-loopback>" in options["args"]
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in options["args"]

    async def test_tab_without_interception_reads_nothing(self) -> None:
        """Защиту не поставить — страницы нет: отказ закрытый, а не открытый."""
        renderer, _ = _renderer(_Unguardable())

        assert await renderer.render(SITE) is None

    async def test_old_playwright_without_socket_interception_says_so(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        context = _Context("<html><body>ok</body></html>", sockets=False)
        renderer, _ = _renderer(context)

        with caplog.at_level(logging.WARNING):
            assert await renderer.render(SITE) is not None

        assert context.routes == ["**/*"]
        assert "без перехвата веб-сокетов" in caplog.text

    async def test_rendered_page_has_the_same_bounds_as_a_fetched_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "MAX_PAGE_BYTES", 1_000)
        monkeypatch.setattr("backend.features.contacts.pages._TAIL_CHARS", 300)
        huge = "<html><body>" + "<p>каталог</p>" * 5_000 + "</body></html>"
        renderer, _ = _renderer(_Context(huge))

        rendered = await renderer.render(SITE)

        assert rendered is not None
        assert len(rendered) < 1_100
        assert rendered.endswith("</body></html>")
