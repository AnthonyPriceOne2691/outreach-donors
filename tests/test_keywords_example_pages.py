"""Главные примеров: путь обхода (robots.txt, пауза, защита исходящих) и отказы словами.

Сеть — подделка `httpx.MockTransport`, сайты — синтетические (`.example.test`):
проверяется наш путь и наши слова, а не чужие сайты. Главные — на разных языках
и письменностях: тема берётся с любой, и снятие текста не должно терять ни одну.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from backend.features.crawl.limiter import DomainLimiter
from backend.features.keywords.example_pages import (
    HEADINGS,
    TEXT_CHARS,
    PageReader,
    page_of,
)
from backend.features.keywords.examples import Example, read_examples
from backend.shared.net import url_guard
from backend.shared.net.url_guard import GuardedTransport

Handler = Callable[[httpx.Request], httpx.Response]


def home(lang: str, title: str, description: str, body: str, *, head: str = "") -> str:
    return (
        f'<!doctype html><html lang="{lang}"><head><title>{title}</title>'
        f'<meta name="description" content="{description}">{head}'
        "<script>var tracking = 'не текст сайта';</script><style>.x{color:red}</style>"
        f"</head><body>{body}</body></html>"
    )


#: Главные на разных языках: немецкий, японский, арабский, русский, испанский.
GARDEN_DE = home(
    "de",
    "Gartenfreunde — Tipps für Garten und Balkon",
    "Pflanzen, Gemüsebeete und Rasenpflege: Ratgeber für Hobbygärtner.",
    "<header><nav><a href='/gemuese'>Gemüse</a><a href='/rasen'>Rasen</a></nav></header>"
    "<main><h1>Gartentipps für jede Jahreszeit</h1><h2>Tomaten richtig pflanzen</h2>"
    "<p>Wir zeigen, wie Sie Tomaten, Gurken und Kräuter erfolgreich anbauen.</p></main>"
    "<footer>Impressum · Datenschutz</footer>",
)
COOKING_JA = home(
    "ja",
    "かんたん家庭料理レシピ",
    "毎日の献立に役立つ和食と洋食のレシピを紹介します。",
    "<nav><a href='/washoku'>和食</a><a href='/youshoku'>洋食</a></nav>"
    "<h1>今週の人気レシピ</h1><h2>肉じゃがの作り方</h2><p>旬の野菜を使った簡単な料理です。</p>",
)
FOOTBALL_AR = home(
    "ar",
    "أخبار كرة القدم اليوم",
    "تغطية شاملة لمباريات الدوري ونتائج الفرق والانتقالات.",
    "<nav><a href='/league'>الدوري</a><a href='/transfers'>الانتقالات</a></nav>"
    "<h1>نتائج مباريات الأسبوع</h1><p>تحليل المباريات وآراء المدربين واللاعبين.</p>",
)
REPAIR_RU = home(
    "ru",
    "Ремонт квартиры своими руками",
    "Советы по отделке, выбору материалов и планировке.",
    "<h1>С чего начать ремонт</h1><h3>Как выбрать ламинат</h3><p>Пошаговые инструкции.</p>",
)
TRAVEL_ES = home(
    "es",
    "Viajar por España: rutas y consejos",
    "",
    "<h1>Rutas por el norte</h1><p>Pueblos, playas y gastronomía local para tus viajes.</p>",
    head='<meta property="og:description" content="Guía de viajes con rutas por España.">',
)


def site(pages: dict[str, httpx.Response], *, robots: httpx.Response | None = None) -> Handler:
    """Сайт по путям: robots.txt (по умолчанию — 404, ограничений нет) и страницы."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return robots if robots is not None else httpx.Response(404)
        return pages.get(request.url.path, httpx.Response(404))

    return handle


def web(sites: dict[str, Handler], seen: list[str]) -> httpx.MockTransport:
    """Интернет из нескольких синтетических сайтов; неизвестное имя не отвечает."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        handler = sites.get(request.url.host)
        if handler is None:
            raise httpx.ConnectError("имя не найдено", request=request)
        return handler(request)

    return httpx.MockTransport(handle)


async def read(sites: dict[str, Handler], *lines: str, seconds: float = 30.0) -> tuple[list, list]:
    seen: list[str] = []
    examples = read_examples(lines)
    async with httpx.AsyncClient(transport=web(sites, seen), timeout=5.0) as client:
        reader = PageReader(client, limiter=DomainLimiter(delay_sec=0), site_seconds=seconds)
        pages = await reader.read_all(examples.sites)
    return pages, seen


def html(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, html=text)


class TestWhatThePageSays:
    @pytest.mark.parametrize(
        ("page", "lang", "title", "heading", "menu", "word"),
        [
            (GARDEN_DE, "de", "Gartenfreunde", "Tomaten richtig pflanzen", "Gemüse", "Kräuter"),
            (COOKING_JA, "ja", "かんたん家庭料理レシピ", "肉じゃがの作り方", "和食", "旬の野菜"),
            (FOOTBALL_AR, "ar", "أخبار كرة القدم", "نتائج مباريات الأسبوع", "الدوري", "المدربين"),
            (REPAIR_RU, "ru", "Ремонт квартиры", "Как выбрать ламинат", "", "Пошаговые"),
        ],
    )
    def test_any_language_keeps_its_script(
        self, page: str, lang: str, title: str, heading: str, menu: str, word: str
    ) -> None:
        """Тема берётся с главной на любом языке — снятие текста не теряет письменность."""
        read = page_of(
            Example(line=1, host="site.example.test"), "https://site.example.test/", page
        )

        assert read.read, read.reason
        assert read.language == lang
        assert title in read.title
        assert heading in read.headings
        if menu:
            assert menu in read.menu
        assert word in read.text

    def test_code_and_styles_are_not_the_site_text(self) -> None:
        read = page_of(Example(1, "garden.example.test"), "https://garden.example.test/", GARDEN_DE)

        assert "tracking" not in read.text
        assert "color:red" not in read.text
        # Шапка, меню и подвал — не текст главной: меню идёт своим полем.
        assert "Impressum" not in read.text

    def test_open_graph_stands_in_for_a_missing_description(self) -> None:
        read = page_of(Example(1, "travel.example.test"), "https://travel.example.test/", TRAVEL_ES)

        assert read.description == "Guía de viajes con rutas por España."

    def test_long_pages_are_cut_to_the_ceilings(self) -> None:
        """Десять главных целиком утопили бы вызов о теме в подвалах сайтов."""
        many = "".join(f"<h2>Раздел номер {number}</h2>" for number in range(40))
        words = " ".join(["слово"] * 3000)
        read = page_of(
            Example(1, "big.example.test"),
            "https://big.example.test/",
            home("ru", "Большой сайт", "Описание", f"{many}<p>{words}</p>"),
        )

        assert len(read.headings) == HEADINGS
        assert len(read.text) <= TEXT_CHARS

    def test_a_script_skeleton_is_refused_not_read(self) -> None:
        skeleton = "<html><head><title>App</title></head><body><div id='root'></div></body></html>"

        read = page_of(Example(1, "app.example.test"), "https://app.example.test/", skeleton)

        assert not read.read
        assert "почти нет текста" in read.reason

    @pytest.mark.parametrize(
        "title", ["Проверка браузера", "Bitte warten", "Attention Required! | Cloudflare"]
    )
    def test_a_protection_stub_is_refused_in_any_language(self, title: str) -> None:
        stub = home("xx", title, "", "<p>" + "подождите немного " * 20 + "</p>")

        read = page_of(Example(1, "stub.example.test"), "https://stub.example.test/", stub)

        assert "заглушка защиты" in read.reason


class TestReader:
    async def test_pages_come_back_in_the_order_of_the_examples(self) -> None:
        pages, _ = await read(
            {
                "garden.example.test": site({"/": html(GARDEN_DE)}),
                "cooking.example.test": site({"/": html(COOKING_JA)}),
                "football.example.test": site({"/": html(FOOTBALL_AR)}),
            },
            "football.example.test",
            "garden.example.test",
            "cooking.example.test",
        )

        assert [page.example.host for page in pages] == [
            "football.example.test",
            "garden.example.test",
            "cooking.example.test",
        ]
        assert all(page.read for page in pages)

    async def test_robots_is_asked_before_the_page(self) -> None:
        """Сначала разрешение, потом страница — как у обхода доноров."""
        pages, seen = await read(
            {
                "garden.example.test": site(
                    {"/": html(GARDEN_DE)}, robots=httpx.Response(200, text="User-agent: *\n")
                )
            },
            "garden.example.test",
        )

        assert pages[0].read
        assert seen == ["https://garden.example.test/robots.txt", "https://garden.example.test/"]

    async def test_robots_forbidding_the_home_page_is_obeyed(self) -> None:
        robots = httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        pages, seen = await read(
            {"closed.example.test": site({"/": html(GARDEN_DE)}, robots=robots)},
            "closed.example.test",
        )

        assert pages[0].reason == "robots.txt запрещает главную — не открываем"
        assert seen == ["https://closed.example.test/robots.txt"], "главную открыли вопреки запрету"

    async def test_unreadable_robots_is_not_consent(self) -> None:
        """Сайт ответил на robots.txt ошибкой — молчание не согласие, как у обхода."""
        pages, seen = await read(
            {"busy.example.test": site({"/": html(GARDEN_DE)}, robots=httpx.Response(503))},
            "busy.example.test",
        )

        assert pages[0].reason.startswith("robots.txt не прочитан (код 503)")
        assert all(url.endswith("/robots.txt") for url in seen)

    async def test_crawl_delay_of_the_site_is_kept_between_requests(self) -> None:
        """`Crawl-delay` сайта сильнее нашей паузы: следующий вид адреса главной ждёт её,
        как следующая страница у обхода (`crawl/walk.py`)."""

        def https_down(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nCrawl-delay: 0.3\n")
            if request.url.scheme == "https":
                return httpx.Response(503)
            return html(GARDEN_DE)

        limiter = DomainLimiter(delay_sec=0, max_delay_sec=10)
        transport = web({"slow.example.test": https_down}, [])
        async with httpx.AsyncClient(transport=transport) as client:
            [example] = read_examples(["slow.example.test"]).sites
            page = await PageReader(client, limiter=limiter).read(example)

        assert page.read
        assert page.url == "http://slow.example.test/"
        assert limiter.delay_for("slow.example.test") == pytest.approx(0.3)
        assert limiter.waited_sec >= 0.25, "следующий вид адреса не выждал паузу сайта"

    async def test_an_unreachable_site_is_named_without_a_robots_story(self) -> None:
        pages, seen = await read({}, "gone.example.test")

        assert pages[0].reason == "не открылся: имя не найдено или соединение отклонено"
        assert all(url.endswith("/robots.txt") for url in seen), "главную искали у мёртвого"

    async def test_a_site_that_is_too_slow_is_cut_by_the_deadline(self) -> None:
        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(404)

        examples = read_examples(["slow.example.test"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            reader = PageReader(client, limiter=DomainLimiter(delay_sec=0), site_seconds=0.2)
            pages = await reader.read_all(examples.sites)

        assert pages[0].reason == "не ответил за 0.2 с"

    async def test_a_read_timeout_is_said_in_words(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            raise httpx.ReadTimeout("молчит", request=request)

        pages, _ = await read({"mute.example.test": timeout}, "mute.example.test")

        assert pages[0].reason == "не ответил вовремя"

    async def test_a_closed_site_is_named_with_its_code(self) -> None:
        pages, _ = await read(
            {"closed.example.test": site({"/": html("<title>403</title>", status=403)})},
            "closed.example.test",
        )

        assert pages[0].reason == "сайт закрылся от нас (код 403)"

    async def test_the_most_telling_refusal_wins_over_a_network_one(self) -> None:
        """Из видов адреса называется самый говорящий: «закрылся» полезнее «не ответил»."""

        def mixed(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            if request.url.host.startswith("www."):
                return httpx.Response(403, html="<title>403</title>")
            raise httpx.ConnectError("отказ", request=request)

        pages, _ = await read(
            {"mixed.example.test": mixed, "www.mixed.example.test": mixed}, "mixed.example.test"
        )

        assert pages[0].reason == "сайт закрылся от нас (код 403)"

    async def test_not_a_page_is_named(self) -> None:
        pdf = httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
        pages, _ = await read({"file.example.test": site({"/": pdf})}, "file.example.test")

        assert pages[0].reason == "по адресу главной не страница, а другой файл (не HTML)"

    async def test_an_antibot_screen_is_not_the_site(self) -> None:
        screen = html("<title>Just a moment...</title><p>Checking your browser</p>")
        pages, _ = await read({"guard.example.test": site({"/": screen})}, "guard.example.test")

        assert pages[0].reason == "вместо главной — заставка защиты от ботов"

    async def test_redirect_to_www_is_followed(self) -> None:
        moved = httpx.Response(301, headers={"location": "https://www.garden.example.test/"})
        pages, _ = await read(
            {
                "garden.example.test": site({"/": moved}),
                "www.garden.example.test": site({"/": html(GARDEN_DE)}),
            },
            "garden.example.test",
        )

        assert pages[0].read
        assert pages[0].url == "https://www.garden.example.test/"

    async def test_a_platform_is_refused_without_a_request(self) -> None:
        pages, seen = await read({}, "www.youtube.com", "m.facebook.com")

        assert all("платформа или соцсеть" in page.reason for page in pages)
        assert seen == [], "к платформе ходили, хотя её главная — не тема ниши"

    async def test_a_name_leading_inside_the_network_is_not_visited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Защита исходящих — та же, что у обхода: имя, ведущее во внутреннюю сеть,
        не открывается, и сборка говорит это словами, а не падает."""
        seen: list[str] = []
        monkeypatch.setattr(url_guard, "_resolved_addresses", lambda host: ["10.0.0.7"])
        transport = GuardedTransport(web({"inside.example.test": site({})}, seen))
        examples = read_examples(["inside.example.test"])
        async with httpx.AsyncClient(transport=transport) as client:
            pages = await PageReader(client, limiter=DomainLimiter(delay_sec=0)).read_all(
                examples.sites
            )

        assert pages[0].reason == "адрес ведёт во внутреннюю сеть — туда не ходим"
        assert seen == []
