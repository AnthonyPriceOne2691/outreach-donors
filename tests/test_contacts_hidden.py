"""Адрес на главной своего домена находится, как бы сайт его ни записал.

Проверка — через лестницу целиком (`ContactLadder.find`), а не через функцию разбора:
адрес входит в систему этим путём, и промах здесь — это донор «адреса нет» на экране
(урок #122: годность и извлечение — разные проверки). Сайты — синтетические, в зоне
`.example.test`; в живую сеть тест не ходит.

Виды записи — из тех, что встречаются у конструкторов сайтов на любом языке: прямой
`mailto:` с регистром и сущностями, процентная запись, слеши после схемы, «[at]» и
«(at)» прямо в ссылке, `<area>` карты-картинки, разметка организации (JSON-LD в
`<head>` — так нашёлся живой промах 20.10.2026), микроразметка и мета-теги, атрибуты
`data-`, склейка строк в скрипте, перевёрнутый стилем текст, обфускация Cloudflare.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.core.domain import ContactStatus

pytestmark = pytest.mark.asyncio

HOST = "shop.example.test"
OWN = f"info@{HOST}"


def _cloudflare(email: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{ord(char) ^ key:02x}" for char in email)


def _home(body: str, head: str = "") -> str:
    return f"<html lang='ru'><head>{head}</head><body><h1>Магазин</h1>{body}</body></html>"


#: Вид записи → главная. Адрес везде один — `info@` на домене сайта.
WRITTEN = {
    "прямой mailto": _home(f'<a href="mailto:{OWN}">Напишите нам</a>'),
    "схема заглавными": _home('<a href="MAILTO:Info@Shop.Example.Test">Почта</a>'),
    "сущности в ссылке": _home(
        '<a href="&#109;&#97;&#105;&#108;&#116;&#111;&#58;&#105;nfo&#64;shop.example.test">'
        "Почта</a>"
    ),
    "процент в ссылке": _home('<a href="mailto:info%40shop.example.test">Почта</a>'),
    "слеши после схемы": _home(f'<a href="mailto://{OWN}">Почта</a>'),
    "[at] в ссылке": _home('<a href="mailto:info[at]shop.example.test">Почта</a>'),
    "(at)(dot) в ссылке": _home('<a href="mailto:info(at)shop(dot)example(dot)test">Почта</a>'),
    "карта-картинка": _home(f'<map name="m"><area shape="rect" href="mailto:{OWN}"></map>'),
    "сущности в тексте": _home("<p>Почта: &#105;&#110;&#102;&#111;&#64;shop.example.test</p>"),
    "[at] [dot] текстом": _home("<p>info [at] shop [dot] example [dot] test</p>"),
    "JSON-LD в head": _home(
        "<p>Каталог</p>",
        head=(
            '<script type="application/ld+json">{"@context":"https://schema.org",'
            f'"@type":"Organization","name":"Магазин","email":"{OWN}"}}</script>'
        ),
    ),
    "JSON-LD с mailto и графом": _home(
        "<p>Каталог</p>",
        head=(
            '<script type="application/ld+json">{"@graph":[{"@type":"LocalBusiness",'
            f'"contactPoint":{{"@type":"ContactPoint","email":"mailto:{OWN}"}}}}]}}</script>'
        ),
    ),
    "JSON-LD с висячей запятой": _home(
        "<p>Каталог</p>",
        head=f'<script type="application/ld+json">{{"email": "{OWN}",}}</script>',
    ),
    "микроразметка meta": _home(
        f'<div itemscope><meta itemprop="email" content="{OWN}"><p>Каталог</p></div>'
    ),
    "микроразметка текстом": _home(f'<p itemprop="email">{OWN}</p>'),
    "og:email": _home("<p>Каталог</p>", head=f'<meta property="og:email" content="{OWN}">'),
    "data-email": _home(f'<button class="copy" data-email="{OWN}">Скопировать почту</button>'),
    "data-mail-user + data-mail-domain": _home(
        '<a href="#" data-mail-user="info" data-mail-domain="shop.example.test">Почта</a>'
    ),
    "data-name + data-email-domain + data-tld": _home(
        '<span data-name="info" data-email-domain="shop.example" data-tld="test">Почта</span>'
    ),
    "data-mailbox + data-domain": _home(
        '<span data-mailbox="info" data-domain="shop.example.test">Почта</span>'
    ),
    "склейка строк в head": _home(
        "<a id='m'>Почта</a>",
        head="<script>var m = 'info' + '@' + 'shop.example.test';</script>",
    ),
    "document.write в теле": _home(
        "<script>document.write('<a href=\"mailto:' + 'info' + '@' + 'shop.example.test' + "
        "'\">Почта</a>')</script>"
    ),
    "экранирование JS": _home("<script>var m = 'info\\u0040shop.example.test';</script>"),
    "%40 текстом": _home("<p>Почта: info%40shop.example.test</p>"),
    "перевёрнутый стилем": _home(
        '<span style="unicode-bidi: bidi-override; direction: rtl">tset.elpmaxe.pohs@ofni</span>'
    ),
    "Cloudflare data-cfemail": _home(
        '<a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
        f'data-cfemail="{_cloudflare(OWN)}">[email&#160;protected]</a>'
    ),
    "Cloudflare в ссылке": _home(
        f'<a href="/cdn-cgi/l/email-protection#{_cloudflare(OWN)}">Почта</a>'
    ),
}


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен принимает почту: ступень MX — не предмет этих тестов."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Сайт-заглушка: путь → (код, тело). Чего нет в карте — 404."""

    def __init__(self, pages: dict[str, tuple[int, str] | str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(str(request.url))
        found = self.pages.get(request.url.path)
        if found is None:
            return httpx.Response(404, text="нет такой страницы")
        status, body = found if isinstance(found, tuple) else (200, found)
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})


async def _find(site: Site, **ladder: object) -> object:
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        return await ContactLadder(http, **ladder).find(HOST)  # type: ignore[arg-type]


@pytest.mark.parametrize("written", list(WRITTEN), ids=list(WRITTEN))
async def test_own_address_on_the_home_page_is_found(written: str) -> None:
    """Каждый вид записи адреса на главной даёт «адрес найден» и сам адрес."""
    result = await _find(Site({"/": WRITTEN[written]}))

    assert result.status is ContactStatus.FOUND  # type: ignore[attr-defined]
    assert result.contact.email == OWN  # type: ignore[attr-defined]
    assert result.contact.page_url == f"https://{HOST}/"  # type: ignore[attr-defined]


async def test_visible_address_wins_over_a_hidden_one() -> None:
    """Адрес из исходника — догадка: прямой адрес той же страницы её побеждает."""
    home = _home(
        "<p>Пишите: sales@shop.example.test</p>",
        head="<script>var m = 'info' + '@' + 'shop.example.test';</script>",
    )
    result = await _find(Site({"/": home}))

    assert result.contact.email == "sales@shop.example.test"  # type: ignore[attr-defined]
    assert not result.contact.guessed  # type: ignore[attr-defined]


class TestNotAnAddressOfTheSite:
    """Исходник шумный: чужое и образцы адресом сайта не становятся."""

    async def test_foreign_address_in_a_script_is_not_taken(self) -> None:
        home = _home("<script>var help = 'support' + '@' + 'widget.example.org';</script>")
        result = await _find(Site({"/": home}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]

    async def test_input_placeholder_is_not_an_address(self) -> None:
        home = _home(
            f'<form><input type="email" placeholder="you@{HOST}" value="me@{HOST}"></form>'
        )
        result = await _find(Site({"/": home}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]

    async def test_structured_markup_still_passes_the_quality_filter(self) -> None:
        """Разметка — прямой адрес, но фильтр качества тот же: `noreply@` не пишем."""
        head = f'<script type="application/ld+json">{{"email": "noreply@{HOST}"}}</script>'
        result = await _find(Site({"/": _home("<p>x</p>", head=head)}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]
        assert any("не принимает ответов" in why for _, why in result.rejected)  # type: ignore[attr-defined]

    async def test_attribute_pair_without_mail_in_its_names_is_not_an_address(self) -> None:
        """Виджет чата: `data-account` + `data-host` — номер и хост, а не ящик (ревью
        20.10.2026). Пара — адрес, только если одно из имён говорит о почте."""
        home = _home(
            f'<div id="chat" data-account="12345" data-host="chat.{HOST}"></div>'
            f'<span data-user="info" data-domain="{HOST}">x</span>'
        )
        result = await _find(Site({"/": home}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "head",
        [
            f'<link rel="preconnect" href="https://user:s3cret@cdn.{HOST}/">',  # pragma: allowlist secret
            f'<script>Sentry.init({{dsn: "https://k3y9public@sentry.{HOST}/2"}})</script>',
        ],
        ids=["логин и пароль в адресе", "ключ телеметрии"],
    )
    async def test_login_in_a_link_is_not_an_address(self, head: str) -> None:
        """«Собака» после `//логин:` — вход в адресе, а не ящик на домене сайта."""
        result = await _find(Site({"/": _home("<p>Каталог</p>", head=head)}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]

    async def test_share_by_mail_link_is_not_an_address(self) -> None:
        """«Поделиться письмом» — `mailto:?subject=…` без адреса: это не ящик сайта."""
        home = _home('<a href="mailto:?subject=Скидка&amp;body=https://x">Поделиться</a>')
        result = await _find(Site({"/": home}))

        assert result.status is ContactStatus.NOT_FOUND  # type: ignore[attr-defined]


class TestGuessDoesNotStopTheSearch:
    """Догадка — не исход, пока не пройдены бесплатные ступени (ревью 20.10.2026): иначе
    адрес из исходника главной остановил бы обход на первой же странице рекламы, и прямой
    адрес со страницы контактов не нашёлся бы."""

    LINKS = '<a href="/advertise/">Реклама</a> <a href="/contact/">Контакты</a>'
    ADVERTISE = _home("<p>Цены — по запросу.</p>")
    CONTACT = _home(f'<a href="mailto:editor@{HOST}">editor@{HOST}</a>')

    @pytest.mark.parametrize(
        "home",
        [
            _home(f'<span data-email="robot@{HOST}"></span>{LINKS}'),
            _home(LINKS, head=f'<script>var cfg = {{"notify": "robot@{HOST}"}};</script>'),
            _home(f"<p>robot [at] shop [dot] example [dot] test</p>{LINKS}"),
        ],
        ids=["data-email", "скрипт в head", "обфускация текстом"],
    )
    async def test_direct_address_further_on_wins(self, home: str) -> None:
        site = Site({"/": home, "/advertise/": self.ADVERTISE, "/contact/": self.CONTACT})
        result = await _find(site)

        assert result.contact.email == f"editor@{HOST}"  # type: ignore[attr-defined]
        assert not result.contact.guessed  # type: ignore[attr-defined]
        assert f"https://{HOST}/contact/" in site.requested

    async def test_guess_does_not_cancel_the_browser(self) -> None:
        """Закрывшийся сайт идёт к браузеру и с догадкой: прямой адрес рисует скрипт."""

        class Renderer:
            async def render(self, url: str) -> str | None:
                return CONTACT_PAGE if url == f"https://{HOST}/" else None

        CONTACT_PAGE = _home(f'<a href="mailto:editor@{HOST}">Почта</a>')
        home = _home(f'<span data-email="robot@{HOST}"></span><a href="/contact/">Контакты</a>')
        site = Site({"/": home, "/contact/": (403, "<p>Forbidden</p>")})
        result = await _find(site, renderer=Renderer())

        assert result.contact.email == f"editor@{HOST}"  # type: ignore[attr-defined]

    async def test_guess_is_the_outcome_when_nothing_direct_is_found(self) -> None:
        """Бесплатные ступени кончились без прямого адреса — догадка и есть исход."""
        result = await _find(Site({"/": _home(f'<span data-email="robot@{HOST}"></span>')}))

        assert result.status is ContactStatus.FOUND  # type: ignore[attr-defined]
        assert result.contact.email == f"robot@{HOST}"  # type: ignore[attr-defined]
        assert result.contact.guessed  # type: ignore[attr-defined]
