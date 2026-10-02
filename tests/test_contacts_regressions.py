"""Лестница контактов: регрессии, найденные ревью #118.

#118 научил обход локальным слагам, и четыре вещи сломались по пути базы,
то есть там, где ищутся контакты доноров на проде:

1. раздел узнавался подстрокой — `uslugi` внутри `gosuslugi`, `o-nas` внутри
   `contract-to-nasa`, — и статья, принятая за раздел рекламы, отдавала
   процитированный в ней чужой адрес как контакт донора;
2. ссылки-заголовки с главной («Tudo sobre o caso…») принимались за разделы
   и съедали бюджет обхода раньше угаданных слагов — домен уходил на платную
   ступень;
3. объявленный `lang="en"` на `.md` или `.de` игнорировался, первыми шли
   чужие слаги, и английские разделы вылетали из бюджета попыток;
4. ящики на зоне `.ovh` и на `sentry.com` отсеивались как хостер и ключ
   телеметрии — а проверка годности идёт и при сборке письма.

Плюс одна поломка другого рода: догадки Skype и WhatsApp откатывались
квадратично на длинном пробельном хвосте и держали цикл событий секундами.

Сквозные проверки идут через саму лестницу на сайте-заглушке: ступень, которая
выбирает не ту страницу, по отдельной функции выглядит исправной.
"""

from __future__ import annotations

import time

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.extract import find_contact_links
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.messengers import harvest_handles
from backend.features.contacts.pages import kind_of, language_hint
from backend.features.contacts.provider import Candidate, Quota
from backend.features.contacts.quality import rejection_reason
from backend.features.contacts.slugs import LINK_MARKERS
from backend.features.core.domain import ContactStatus, PageKind


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен принимает почту: здесь проверяется обход страниц, а не ступень 0."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Сайт-заглушка: заданные страницы, остальное — 404, запросы считаются."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(request.url.path)
        if "rdap.org" in str(request.url):
            return httpx.Response(404, json={"errorCode": 404})
        body = self.pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="нет такой страницы")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})


class CountingProvider:
    """Платная ступень. Обращение к ней в этих сценариях — уже поломка."""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.calls.append(host)
        return []


def _page(body: str, *, lang: str | None = None) -> str:
    declared = f' lang="{lang}"' if lang else ""
    return f"<html{declared}><body>{body}</body></html>"


class TestArticleIsNotASection:
    """Находка 1: вид страницы — по словам адреса, а не по подстроке."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://site.ru/news/kak-zapisatsya-cherez-gosuslugi/",
            "https://site.com/2024/05/spacex-hands-contract-to-nasa/",
            "https://site.com.br/noticias/tudo-sobre-o-caso-1/",
            "https://site.ru/news/novye-pravila-parkovki/",
            "https://site.com/wordpress-tips/",
            # Живой прогон 30.09.2026: `press` внутри «чеснокодавки» делал
            # обзор кухонной утвари страницей контактов сайта газеты.
            "https://zeitung.de/kaufkompass/test/die-beste-knoblauchpresse/",
            # Формы слова не делают заголовок разделом: шесть слов — статья.
            "https://site.com/news/guest-post-drama-at-the-oscars/",
        ],
    )
    def test_article_is_home_weight(self, url: str) -> None:
        assert kind_of(url) is PageKind.HOME

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://site.ru/kontakty/", PageKind.CONTACT),
            ("https://site.com/contact-us-2/", PageKind.CONTACT),
            ("https://site.com/contactus.html", PageKind.CONTACT),
            ("https://site.com/index.php?page=contact", PageKind.CONTACT),
            ("https://site.id/p/hubungi-kami", PageKind.CONTACT),
            ("https://site.de/kontakt-impressum/", PageKind.CONTACT),
            ("https://site.ru/reklama-na-sayte/", PageKind.MONEY),
            ("https://site.com/advertise-with-us/", PageKind.MONEY),
            ("https://site.com/meet-the-team/", PageKind.ABOUT),
            # Длинное слово раздела узнаётся внутри слова: `/contacta/` живой
            # прогон 30.09.2026 потерял при точном сравнении, подстрока — нет.
            ("https://reisen.es/contacta/", PageKind.CONTACT),
            ("https://site.de/pressekontakt/", PageKind.CONTACT),
            ("https://site.de/kontaktformular/", PageKind.CONTACT),
            ("https://shop.com/SupportCenter/", PageKind.CONTACT),
            # А `press` внутри `impressum` — нет: это раздел «о нас» по словарю.
            ("https://zeitung.de/service/impressum/", PageKind.ABOUT),
            # Ревью #120: множественное число в многословном слаге. Для
            # гест-постинга это самые ценные страницы, и первая версия правки
            # их теряла: `posts` не равно `post`.
            ("https://site.com/guest-posts/", PageKind.MONEY),
            ("https://site.com/sponsored-posts/", PageKind.MONEY),
            ("https://site.com/submit-guest-posts/", PageKind.MONEY),
            ("https://site.com/submit-articles/", PageKind.MONEY),
            ("https://site.com/mediakit/", PageKind.MONEY),
            ("https://site.com/mediakits/", PageKind.MONEY),
            ("https://site.com/guestposts.html", PageKind.MONEY),
            ("https://site.com/writeforus/", PageKind.MONEY),
            # Ревью #120: разделы продажи размещения, которых в словаре не было.
            ("https://site.com/sponsorship/", PageKind.MONEY),
            ("https://site.com/sponsored-content/", PageKind.MONEY),
            ("https://site.com/work-with-me/", PageKind.MONEY),
            ("https://site.com/partner-with-us/", PageKind.MONEY),
            ("https://site.com/contribute/", PageKind.MONEY),
            ("https://site.com/become-a-contributor/", PageKind.MONEY),
            ("https://site.com/guest-blogging/", PageKind.MONEY),
            ("https://site.com/guest-author/", PageKind.MONEY),
            ("https://site.com/rate-card/", PageKind.MONEY),
            # Живой прогон 30.09.2026: немецкое `presse` подстрока узнавала через
            # `press`, сравнение по словам — нет; теперь оно в словаре.
            ("https://site.de/presse/", PageKind.CONTACT),
        ],
    )
    def test_section_is_still_recognised(self, url: str, kind: PageKind) -> None:
        """Обратная сторона: по словам не теряются настоящие разделы."""
        assert kind_of(url) is kind

    async def test_address_from_an_article_is_not_the_donor_contact(self) -> None:
        """До правки статья шла впереди как «реклама» и обрывала обход своим адресом."""
        site = Site(
            {
                "/": _page(
                    '<a href="/news/kak-zapisatsya-cherez-gosuslugi/">Как записаться через'
                    ' Госуслуги</a> <a href="/kontakty/">Контакты</a>',
                    lang="ru",
                ),
                "/news/kak-zapisatsya-cherez-gosuslugi/": _page(
                    "Пишите в поддержку: support@gosuslugi.ru"
                ),
                "/kontakty/": _page('<a href="mailto:reklama@site.ru">реклама</a>'),
            }
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("site.ru")

        assert result.contact is not None
        assert result.contact.email == "reklama@site.ru"
        assert "/news/kak-zapisatsya-cherez-gosuslugi/" not in site.requested


class TestHeadlinesDoNotEatTheBudget:
    """Находка 2: ссылка с главной — раздел, только если её так и называют."""

    def test_headline_links_are_not_sections(self) -> None:
        headlines = "".join(
            f'<a href="/noticias/tudo-sobre-o-caso-{n}/">Tudo sobre o caso {n}</a>'
            for n in range(10)
        )
        html = f'{headlines}<a href="/contato/">Contato</a>'

        assert find_contact_links(html, slugs=LINK_MARKERS) == {"/contato/"}

    @pytest.mark.parametrize(
        ("href", "text"),
        [
            ("/p/1", "Tentang Kami"),
            ("/p/2", "Hubungi Kami"),
            ("/p/3", "О нас"),
            ("/p/4", "Advertise with us"),
            ("/p/5", "Redaksi"),
            ("/guest-posts/", "Guest Posts"),
            # WordPress без ЧПУ живёт только текстом ссылки, а в тексте бывает
            # имя издания: длинное слово раздела в начале фразы — раздел.
            ("/?page_id=12", "Advertise with The Verge"),
            ("/?page_id=13", "Contact the editorial team"),
        ],
    )
    def test_short_section_names_are_links(self, href: str, text: str) -> None:
        assert find_contact_links(f'<a href="{href}">{text}</a>', slugs=LINK_MARKERS) == {href}

    @pytest.mark.parametrize(
        ("href", "text"),
        [
            ("/p/6", "О насилии"),
            ("/profile/john-smith/", "John Smith"),
            ("/news/novye-pravila-parkovki/", "Новые правила парковки"),
        ],
    )
    def test_words_inside_other_words_are_not_links(self, href: str, text: str) -> None:
        assert find_contact_links(f'<a href="{href}">{text}</a>', slugs=LINK_MARKERS) == set()

    async def test_contact_page_is_reached_past_ten_headlines(self) -> None:
        """До правки десять открытых статей съедали восемь страниц бюджета."""
        headlines = "".join(
            f'<a href="/noticias/tudo-sobre-o-caso-{n}/">Tudo sobre o caso {n}</a>'
            for n in range(10)
        )
        pages = {
            f"/noticias/tudo-sobre-o-caso-{n}/": _page("Notícia sem endereço.") for n in range(10)
        }
        site = Site(
            {
                "/": _page(headlines, lang="pt-BR"),
                **pages,
                "/contato/": _page('<a href="mailto:redacao@site.com.br">escreva</a>'),
            }
        )
        provider = CountingProvider()

        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http, provider=provider).find("site.com.br")

        assert result.status is ContactStatus.FOUND
        assert result.contact is not None
        assert result.contact.email == "redacao@site.com.br"
        assert provider.calls == []
        assert not any(path.startswith("/noticias/") for path in site.requested)


class TestDeclaredLanguage:
    """Находка 3: объявленный язык — ответ, зона — только когда его нет."""

    @pytest.mark.parametrize(
        ("lang", "host"),
        [("en", "example.md"), ("en", "example.de"), ("en-US", "site.ua"), ("uk", "site.ua")],
    )
    def test_declared_language_without_local_slugs_is_not_overridden(
        self, lang: str, host: str
    ) -> None:
        assert language_hint(f'<html lang="{lang}"></html>', host) is None

    @pytest.mark.parametrize("host", ["сайт.рф", "xn--80aswg.xn--p1ai"])
    def test_cyrillic_zone_is_found_in_either_spelling(self, host: str) -> None:
        assert language_hint("<html><body></body></html>", host) == "ru"

    async def test_english_site_on_a_russian_zone_gets_english_slugs(self) -> None:
        """До правки русские слаги шли первыми, и /contact-us/ не влезал в 24 попытки."""
        site = Site(
            {
                "/": _page("No links here.", lang="en"),
                "/contact-us/": _page('<a href="mailto:info@example.md">write</a>'),
            }
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("example.md")

        assert result.contact is not None
        assert result.contact.email == "info@example.md"


class TestZoneIsNotTheHoster:
    """Находка 4: публичный суффикс — не имя хостера, apex — не поддомен."""

    @pytest.mark.parametrize(
        "email", ["contact@monsite.ovh", "info@sentry.com", "contact@sentry.co.uk"]
    )
    def test_site_mailbox_is_accepted(self, email: str) -> None:
        assert rejection_reason(email) is None

    @pytest.mark.parametrize(
        ("email", "marker"),
        [
            ("key@sentry.wixpress.com", "поддомен Sentry"),
            ("support@beget.com", "адрес хостера"),
            ("x@server.beget.tech", "адрес хостера"),
            ("support@ovh.com", "адрес хостера"),
        ],
    )
    def test_hoster_and_telemetry_are_still_rejected(self, email: str, marker: str) -> None:
        reason = rejection_reason(email)
        assert reason is not None
        assert marker in reason


class TestGuessesStayLinear:
    """Догадки Skype и WhatsApp не держат цикл событий на пробельном хвосте.

    До правки 32 тыс. пробелов после «WhatsApp» стоили ~9 с, после «Skype» —
    ~4 с (замер 30.09.2026), и это синхронно, внутри обхода: соседние домены
    ловили таймауты. Потолок в секунду — с запасом на нагруженную машину;
    линейный разбор укладывается в миллисекунды, квадратичный — в десятки секунд.
    """

    def test_long_whitespace_after_trigger_is_cheap(self) -> None:
        tail = " " * 50_000
        html = _page(f"<div>WhatsApp{tail}<svg></svg></div><div>Skype{tail}<b>…</b></div>")

        started = time.perf_counter()
        harvest_handles(html, page_kind=PageKind.HOME)
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0


_HANDLES_SITE = {
    "/": _page('<a href="https://t.me/adsdesk">Telegram</a> <a href="/contact/">Contact</a>'),
    "/contact/": _page('<a href="mailto:info@site.com">write</a>'),
}


class TestHandlesOnlyWhereRead:
    """Каналы связи собирает только прогон по файлу: путь базы их не хранит.

    До правки путь базы проходил каждую страницу вторым разбором ради хэндлов,
    которые выбрасывал: цена без пользы — и та самая квадратичная догадка.
    """

    async def test_database_path_does_not_collect_handles(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(Site(_HANDLES_SITE))) as http:
            result = await ContactLadder(http).find("site.com")

        assert result.contact is not None
        assert result.handles == ()

    async def test_file_sweep_collects_handles(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(Site(_HANDLES_SITE))) as http:
            result = await ContactLadder(http, collect_handles=True).find("site.com")

        assert {handle.value for handle in result.handles} >= {"adsdesk"}
