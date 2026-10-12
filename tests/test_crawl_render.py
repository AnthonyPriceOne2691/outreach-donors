"""Сервис рендеринга — только для страниц, вернувшихся пустыми, и только управляемый.

Включить рендеринг на всё — сжечь бюджет обхода за дни. Проверяется: что считается пустой (скелет
приложения — да, раздел со списком — нет), адаптер шаблоном адреса, ключ не уходит ни
в запись обхода, ни в журнал, вызов — платный уровень под журналом и потолком, а без
ключа сервиса нет вовсе и всё как прежде.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from backend.config import crawl as crawl_cfg
from backend.features.crawl.fetch import CascadeLevel, FetchOutcome, PageCascade
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.proxies import ProxyPool
from backend.features.crawl.render import RenderService, looks_empty
from backend.features.crawl.spend import RENDER_OPERATION
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_proxies import Ledger

HTML = {"content-type": "text/html; charset=utf-8"}
KEY = "sk+live/123=="
TEMPLATE = "https://render.example.test/v1/?api_key={key}&render_js=true&url={url}"

SHELL = (
    "<html><head><title>Ставки</title><script>window.__APP__={}</script></head>"
    '<body><div id="root"></div><script src="/app.js"></script>'
    "<noscript>You need to enable JavaScript to run this app.</noscript></body></html>"
)
CHROME_ONLY = (
    "<html><body><header>" + "Главная Новости Ставки Бонусы Контакты " * 10 + "</header>"
    '<main><div class="skeleton"></div></main>'
    "<footer>" + "© Донор. Все права защищены. Политика конфиденциальности. " * 10 + "</footer>"
    "</body></html>"
)
ARTICLE = (
    "<html><body><nav>Меню</nav><article><h1>Обзор букмекера</h1>"
    + "<p>"
    + "Подробный текст обзора с цифрами и выводами. " * 20
    + '<a href="https://adv.example.test/offer" rel="sponsored">бонус</a></p>'
    + "</article></body></html>"
)
LISTING = (
    "<html><body><div class='posts'>"
    + "".join(
        f'<h2><a href="/post/{n}">Заголовок статьи номер {n} о ставках</a></h2>' for n in range(30)
    )
    + "</div></body></html>"
)
RENDERED = ARTICLE.replace("Обзор букмекера", "Обзор, нарисованный скриптом")


class TestEmptyPage:
    @pytest.mark.parametrize("html", [SHELL, CHROME_ONLY, "<html><body></body></html>"])
    def test_skeleton_and_chrome_only_are_empty(self, html: str) -> None:
        assert looks_empty(html) is True

    @pytest.mark.parametrize("html", [ARTICLE, LISTING])
    def test_article_and_listing_are_not(self, html: str) -> None:
        """Раздел со списком — не статья, но и не пустая: рендеринг его не изменит,
        а стоит денег."""
        assert looks_empty(html) is False


def _service(handler: object, *, template: str = TEMPLATE) -> tuple[RenderService, list[str]]:
    called: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return handler(request)  # type: ignore[operator]

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return RenderService(client, template=template, key=KEY), called


def _answers(html: str, status: int = 200) -> object:
    return lambda _request: httpx.Response(status, text=html, headers=HTML)


class TestAdapter:
    @pytest.mark.parametrize(
        ("template", "words"),
        [
            ("", "адрес API — нет"),
            ("http://render.example.test/?k={key}&u={url}", "не https"),
            ("https://render.example.test/?k={key}", "нет {url}"),
            # Опечатка в схеме: разбор видит https без хоста, и защита исходящих отказала бы
            # адресу текстом с адресом целиком — вместе с ключом.
            ("https:/render.example.test?api_key={key}&url={url}", "нет хоста"),
            ("https://{key}@render.example.test/?url={url}", "нет хоста"),
        ],
    )
    def test_template_that_cannot_work_is_said_in_words(self, template: str, words: str) -> None:
        service, _ = _service(_answers(""), template=template)

        assert service.problem is not None
        assert words in service.problem

    async def test_any_failure_of_the_call_says_only_its_kind(self) -> None:
        """Отказ защиты исходящих — `ValueError` с адресом целиком, то есть с ключом. До правки
        он уходил мимо `except` и ронял обход, а текст — в запись обхода и журнал."""

        def guard(request: httpx.Request) -> httpx.Response:
            raise ValueError(f"в адресе нет хоста: {request.url}")

        service, _ = _service(guard)

        html, reason = await service.render("https://donor.example.test/a")

        assert html is None
        assert reason == "сервис рендеринга не ответил (ValueError)"

    async def test_key_goes_as_a_header_when_the_template_has_no_place_for_it(self) -> None:
        """Сервисы переводят ключ из параметра в заголовок — шаблон без `{key}` так и работает."""
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=RENDERED, headers=HTML)

        service, _ = _service(answer, template="https://render.example.test/v1/?url={url}")

        html, _reason = await service.render("https://donor.example.test/a")

        assert service.problem is None
        assert html == RENDERED
        assert seen[0].headers["authorization"] == f"Bearer {KEY}"
        assert KEY not in str(seen[0].url)

    async def test_key_in_the_template_is_not_also_sent_as_a_header(self) -> None:
        seen: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=RENDERED, headers=HTML)

        service, _ = _service(answer)

        await service.render("https://donor.example.test/a")

        assert "authorization" not in seen[0].headers

    def test_page_and_key_are_encoded_into_the_address(self) -> None:
        """`&` в адресе страницы оборвал бы параметр, `+` в ключе пришёл бы пробелом."""
        service, _ = _service(_answers(""))

        address = service.address("https://donor.example.test/a?b=1&c=2")

        assert service.problem is None
        assert "url=https%3A%2F%2Fdonor.example.test%2Fa%3Fb%3D1%26c%3D2" in address
        assert "api_key=sk%2Blive%2F123%3D%3D" in address

    async def test_answer_is_the_page_and_refusal_is_words_without_the_key(self) -> None:
        good, _ = _service(_answers(RENDERED))
        bad, _ = _service(_answers("quota", status=402))

        def broken(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach {TEMPLATE.replace('{key}', KEY)}")

        down, _ = _service(broken)

        assert await good.render("https://donor.example.test/a") == (RENDERED, None)
        assert await bad.render("https://donor.example.test/a") == (
            None,
            "сервис рендеринга ответил 402",
        )
        html, reason = await down.render("https://donor.example.test/a")
        assert html is None
        assert reason is not None
        assert KEY not in reason
        assert "ConnectError" in reason

    async def test_request_line_in_the_log_carries_no_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """httpx пишет адрес запроса в журнал — ключ из него вычищен фильтром логгера."""
        monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", KEY)
        service, _ = _service(_answers(RENDERED))

        with caplog.at_level(logging.INFO, logger="httpx"):
            await service.render("https://donor.example.test/a")

        lines = [record.getMessage() for record in caplog.records if record.name == "httpx"]
        assert lines, "httpx пишет строку о запросе"
        assert all("sk%2Blive" not in line and KEY not in line for line in lines)
        assert any("***" in line for line in lines)

    def test_no_key_no_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", "")
        monkeypatch.setattr(crawl_cfg, "RENDER_API_URL", TEMPLATE)

        assert RenderService.from_settings(httpx.AsyncClient()) is None


def _cascade(
    page: str, service: RenderService | None, ledger: Ledger | None
) -> tuple[PageCascade, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_answers(page)))  # type: ignore[arg-type]
    cascade = PageCascade(
        client,
        browser_enabled=False,
        proxies=ProxyPool([]),
        render=service,
        spending=ledger,
    )
    return cascade, client


class TestCascade:
    async def test_three_refusals_in_a_row_switch_the_service_off_for_the_crawl(self) -> None:
        """Каждая пустая страница ждала бы сервис до минуты, а ответ был бы тот же."""
        service, called = _service(_answers("quota", status=402))
        ledger = Ledger()
        cascade, client = _cascade(SHELL, service, ledger)

        async with client:
            for path in ("a", "b", "c", "d", "e"):
                await cascade.get(f"https://donor.example.test/{path}")

        assert len(called) == 3
        assert ledger.records == [(RENDER_OPERATION, 1)] * 3, "попытки оплачены"
        noted = cascade.degradation.as_dict()[CascadeLevel.RENDER.value]
        assert "отказал 3 раза подряд — до конца обхода не зовётся" in noted
        assert "402" in noted

    async def test_one_success_resets_the_count(self) -> None:
        answers = iter([402, 402, 200, 402, 402, 200])

        def flaky(_request: httpx.Request) -> httpx.Response:
            status = next(answers)
            return httpx.Response(status, text=RENDERED if status == 200 else "", headers=HTML)

        service, called = _service(flaky)
        cascade, client = _cascade(SHELL, service, Ledger())

        async with client:
            for path in "abcdef":
                await cascade.get(f"https://donor.example.test/{path}")

        assert len(called) == 6, "подряд двух отказов мало, чтобы выключить"

    async def test_empty_page_is_rendered_and_paid(self) -> None:
        service, called = _service(_answers(RENDERED))
        ledger = Ledger()
        cascade, client = _cascade(SHELL, service, ledger)

        async with client:
            result = await cascade.get("https://donor.example.test/a")

        assert (result.outcome, result.level) == (FetchOutcome.OK, CascadeLevel.RENDER)
        assert result.url == "https://donor.example.test/a", "наружу — адрес страницы, не API"
        assert result.html is not None
        assert "нарисованный скриптом" in result.html
        assert len(called) == 1
        assert ledger.records == [(RENDER_OPERATION, 1)]
        assert cascade.by_level[CascadeLevel.RENDER] == 1

    async def test_service_that_did_not_help_keeps_the_page_and_is_still_paid(self) -> None:
        """Сервис берёт за попытку — трата пишется и тогда, когда он не помог."""
        service, _ = _service(_answers(SHELL))
        ledger = Ledger()
        cascade, client = _cascade(SHELL, service, ledger)

        async with client:
            result = await cascade.get("https://donor.example.test/a")

        assert result.level is CascadeLevel.HTTP
        assert ledger.records == [(RENDER_OPERATION, 1)]

    async def test_page_with_text_never_reaches_the_service(self) -> None:
        service, called = _service(_answers(RENDERED))
        ledger = Ledger()
        cascade, client = _cascade(LISTING, service, ledger)

        async with client:
            result = await cascade.get("https://donor.example.test/a")

        assert result.level is CascadeLevel.HTTP
        assert called == []
        assert ledger.asked == []

    @pytest.mark.parametrize(
        ("ledger", "template", "words"),
        [
            (None, TEMPLATE, "без журнала расхода"),
            (Ledger(refused="потолок Этапа 2 на обход выбран"), TEMPLATE, "потолок Этапа 2"),
            (Ledger(), "https://render.example.test/?k={key}", "нет {url}"),
        ],
    )
    async def test_service_is_not_called_when_it_must_not_be_and_the_crawl_says_why(
        self, ledger: Ledger | None, template: str, words: str
    ) -> None:
        service, called = _service(_answers(RENDERED), template=template)
        cascade, client = _cascade(SHELL, service, ledger)

        async with client:
            result = await cascade.get("https://donor.example.test/a")

        assert result.level is CascadeLevel.HTTP
        assert called == []
        assert words in cascade.degradation.as_dict()[CascadeLevel.RENDER.value]

    async def test_without_a_service_everything_is_as_before(self) -> None:
        cascade, client = _cascade(SHELL, None, Ledger())

        async with client:
            result = await cascade.get("https://donor.example.test/a")

        assert result.level is CascadeLevel.HTTP
        assert cascade.degradation.as_dict() == {}
        assert CascadeLevel.RENDER not in cascade.degradation.asked


async def test_rendered_page_gives_its_links_to_the_crawl() -> None:
    """Ради этого и рендерим: у скелета ссылок нет, у нарисованной страницы — есть."""
    sitemap = "<urlset><url><loc>https://donor.example.test/app</loc></url></urlset>"

    def site(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if path.endswith(".xml"):
            return httpx.Response(200, text=sitemap, headers=HTML)
        if path == "/":
            return httpx.Response(
                200, text=ARTICLE.replace("adv.example.test", "home.example.test"), headers=HTML
            )
        return httpx.Response(200, text=SHELL, headers=HTML)

    service, called = _service(_answers(RENDERED))
    ledger = Ledger()
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
        cascade = PageCascade(
            client, browser_enabled=False, proxies=ProxyPool([]), render=service, spending=ledger
        )
        crawler = DonorCrawler(
            client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0), cascade=cascade
        )
        report = await crawler.crawl("donor.example.test")

    assert len(called) == 1
    assert report.by_level["render"] == 1
    assert {link.target_root for link in report.links} >= {"adv.example.test"}
