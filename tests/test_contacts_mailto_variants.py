"""Тринадцать видов главной с `mailto:` на своём домене — и три находки продаж к ним.

Пробу собрал агент продаж 20.10.2026 (13 вариантов главной через `ContactLadder` на
заглушке сайта); здесь она — обычными тестами, с синтетическими именами. Плюс находки:

1. домен Юникодом (`info@пекарня-пример.рф`) шёл в отсев «не похож на адрес» — теперь он
   переводится в punycode, а сверка «свой домен» сравнивает одно с одним;
2. подвал страницы больше предела разбора терялся — теперь разбираются голова и хвост;
3. `mailto:` вне ссылки — в `onclick`, в `action` формы, в скрипте — не читался.

Локальная часть Юникодом (EAI) остаётся отказом: такие адреса принимают не все почтовые
сервисы, и письмо на него отбилось бы.
"""

from __future__ import annotations

import httpx
import pytest
from backend.config import contacts as cfg
from backend.features.contacts import mx, pages
from backend.features.contacts.extract import ascii_domain, extract_emails
from backend.features.contacts.hidden import extract_hidden
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.pages import bounded
from backend.features.contacts.quality import on_site, trusted_guess
from backend.features.core.domain import ContactStatus

HOST = "baeckerei-sample.example.test"
IDN_UNICODE = "пекарня-пример.рф"
IDN = "xn----7sbocojrfofidd7r.xn--p1ai"
OWN = f"info@{HOST}"

#: Длиннее предела разбора — подвал за ним.
BIG = "<p>" + "Brot und Brötchen, frisch gebacken. " * 60_000 + "</p>"

#: Проба продаж: вид → тело главной. Хост — `HOST`, у вариантов `idn_*` — `IDN`.
PROBE = {
    "plain": f'<a href="mailto:{OWN}">Mail</a>',
    "query": f'<a href="mailto:{OWN}?subject=Anfrage&body=Hallo">Mail</a>',
    "upper": f'<a href="MAILTO:Info@{HOST.upper()}">Mail</a>',
    "pct": f'<a href="mailto:info%40{HOST}">Mail</a>',
    "entity": f'<a href="mailto:&#105;&#110;&#102;&#111;&#64;{HOST}">Mail</a>',
    "spaces": f'<a href="  mailto: {OWN} ">Mail</a>',
    "footer_big": BIG + f'<footer><a href="mailto:{OWN}">Mail</a></footer>',
    "idn_unicode": f'<a href="mailto:info@{IDN_UNICODE}">почта</a>',
    "idn_pct": (
        '<a href="mailto:info@%D0%BF%D0%B5%D0%BA%D0%B0%D1%80%D0%BD%D1%8F-'
        '%D0%BF%D1%80%D0%B8%D0%BC%D0%B5%D1%80.%D1%80%D1%84">почта</a>'
    ),
    "area": f'<map><area href="mailto:{OWN}" shape="rect"></map>',
    "button_onclick": f"<button onclick=\"location.href='mailto:{OWN}'\">Mail</button>",
    "data_attr": f'<a data-email="{OWN}" href="#">Mail</a>',
    "js_concat": (
        f'<script>var u="info"; var d="{HOST}"; '
        'document.write("<a href=mailto:"+u+"@"+d+">x</a>");</script>'
    ),
}


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


async def _home(host: str, body: str, *, head: str = "") -> LadderResult:
    """Главная `host` с телом `body`; остальное — 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            page = f"<html><head>{head}</head><body>{body}</body></html>"
            return httpx.Response(200, html=page, request=request)
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await ContactLadder(http).find(host)


class TestProbe:
    pytestmark = pytest.mark.asyncio

    @pytest.mark.parametrize("variant", list(PROBE), ids=list(PROBE))
    async def test_every_variant_finds_the_address(self, variant: str) -> None:
        idn = variant.startswith("idn")
        result = await _home(IDN_UNICODE if idn else HOST, PROBE[variant])

        assert result.status is ContactStatus.FOUND, result.rejected
        assert result.contact is not None
        assert result.contact.email == (f"info@{IDN}" if idn else OWN)


class TestIdn:
    def test_unicode_domain_goes_to_punycode(self) -> None:
        assert ascii_domain(IDN_UNICODE) == IDN
        assert ascii_domain(IDN) == IDN
        assert ascii_domain("Site.Example.Test") == "site.example.test"

    def test_idna_2008_keeps_letters_idna_2003_would_change(self) -> None:
        """Кодек Python — IDNA 2003: `ß` у него `ss`, то есть чужой домен (ревью 20.10.2026)."""
        assert ascii_domain("straße.de") == "xn--strae-oqa.de"
        assert ascii_domain("ς.gr") == "xn--3xa.gr"

    def test_unconvertible_domain_stays_as_written(self) -> None:
        """Пустая метка IDNA не переводит — домен остаётся, отказ назовёт проверка годности."""
        assert ascii_domain("пример..рф") == "пример..рф"

    def test_address_in_text_with_a_unicode_domain_is_found(self) -> None:
        html = f"<p>Пишите нам: info@{IDN_UNICODE}, ответим за день.</p>"

        assert extract_emails(html) == {f"info@{IDN}"}

    def test_cyrillic_prose_does_not_turn_into_addresses(self) -> None:
        html = (
            "<p>Встреча в 10:00 у входа. Почта@офис без точки, «цена@скидка» и "
            "логин@ — не адреса. Курс: 1@2. Скидка 5%@всё.</p>"
        )

        assert extract_emails(html) == set()

    def test_latin_address_before_a_glued_cyrillic_word_is_kept(self) -> None:
        """Латинский адрес, слипшийся с кириллицей после точки, остаётся прежним."""
        html = "<p>Пишите: info@site.example.test.Ответим за день</p>"

        assert "info@site.example.test" in extract_emails(html)

    async def test_unicode_local_part_stays_refused(self) -> None:
        result = await _home(HOST, '<a href="mailto:почта@baeckerei-sample.example.test">x</a>')

        assert result.status is ContactStatus.NOT_FOUND
        assert any("не похож на адрес" in why for _, why in result.rejected)

    def test_own_domain_is_one_name_in_both_spellings(self) -> None:
        """Хост сайта в базе бывает Юникодом, адрес — в punycode: это один домен."""
        assert on_site(IDN, IDN_UNICODE)
        assert on_site(f"mail.{IDN}", IDN_UNICODE)
        assert trusted_guess(f"info@{IDN}", site_host=IDN_UNICODE)
        assert not on_site("other.example.test", IDN_UNICODE)

    async def test_hidden_own_address_on_an_idn_site_is_trusted(self) -> None:
        head = f"<script>var m = 'info' + '@' + '{IDN_UNICODE}';</script>"
        result = await _home(IDN_UNICODE, "<p>Пекарня</p>", head=head)

        assert result.status is ContactStatus.FOUND
        assert result.contact is not None
        assert result.contact.email == f"info@{IDN}"


class TestBigPage:
    def test_small_page_is_whole(self) -> None:
        assert bounded("<p>x</p>") == "<p>x</p>"

    def test_big_page_keeps_head_and_tail_within_the_limit(self) -> None:
        page = "<head>голова</head>" + "x" * (cfg.MAX_PAGE_BYTES * 2) + "<footer>подвал</footer>"

        kept = bounded(page)

        assert len(kept) <= cfg.MAX_PAGE_BYTES + 100
        assert kept.startswith("<head>голова</head>")
        assert kept.endswith("<footer>подвал</footer>")

    def test_tail_does_not_start_inside_an_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Хвост, начатый посреди `mailto:sales@…`, дал бы `es@…` (ревью 20.10.2026): срез
        сдвигается к тегу или пробелу."""
        monkeypatch.setattr(cfg, "MAX_PAGE_BYTES", 10_000)
        monkeypatch.setattr(pages, "_TAIL_CHARS", 3_000)
        link = f'<a href="mailto:sales@{HOST}">sales@{HOST}</a>'
        # Срез хвоста — на третьем знаке «sales» в адресе ссылки: после него в хвост
        # входят остаток ссылки и подвал ровно нужной длины.
        rest = pages._TAIL_CHARS - (len(link) - link.index("sales") - 3)
        footer = ("<p>" + "y " * pages._TAIL_CHARS)[: rest - len("</p>")] + "</p>"
        doc = "<p>" + "x " * 6_000 + "</p>" + link + footer
        assert doc[len(doc) - pages._TAIL_CHARS :].startswith(f"es@{HOST}")

        kept = bounded(doc)

        cut = {email.split("@")[0] for email in extract_emails(kept)}
        cut |= {email.split("@")[0] for email in extract_hidden(kept, site_host=HOST)}
        assert cut <= {"sales"}

    def test_head_does_not_end_inside_an_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Голова, оборванная на `sales@baeckerei-sample.exa`, дала бы адрес с чужой зоной."""
        monkeypatch.setattr(cfg, "MAX_PAGE_BYTES", 10_000)
        monkeypatch.setattr(pages, "_TAIL_CHARS", 3_000)
        cut_at = cfg.MAX_PAGE_BYTES - pages._TAIL_CHARS
        address = f"sales@{HOST}"
        words = "<p>" + "Brot " * 2_000
        # Срез головы — посреди зоны адреса: «…@baeckerei-sample.exa».
        lead = words[: cut_at - address.index(".exa") - 4 - 1] + " "
        doc = lead + address + " </p>" + "<p>" + "z " * 6_000 + "</p>"
        assert doc[cut_at - 4 : cut_at] in address

        kept = bounded(doc)

        assert all(email == address for email in extract_emails(kept))

    @pytest.mark.asyncio
    async def test_head_markup_and_footer_link_both_survive(self) -> None:
        head = f'<script type="application/ld+json">{{"email": "sales@{HOST}"}}</script>'
        body = BIG + f'<footer><a href="mailto:{OWN}">Mail</a></footer>'
        result = await _home(HOST, body, head=head)

        assert result.status is ContactStatus.FOUND
        assert {candidate.email for candidate in result.candidates} == {OWN, f"sales@{HOST}"}


class TestMailtoOutsideALink:
    pytestmark = pytest.mark.asyncio

    async def test_onclick_mailto_is_a_direct_address_on_any_domain(self) -> None:
        """`mailto:` в атрибуте элемента — поле адреса, как `<a href>`: ящик владельца на
        бесплатной почте — тоже адрес."""
        body = "<button onclick=\"window.location='mailto:owner.name@gmail.com'\">Mail</button>"
        result = await _home(HOST, body)

        assert result.contact is not None
        assert result.contact.email == "owner.name@gmail.com"
        assert not result.contact.guessed

    async def test_form_action_mailto_is_an_address(self) -> None:
        body = f'<form action="mailto:{OWN}" method="post"><textarea></textarea></form>'
        result = await _home(HOST, body)

        assert result.contact is not None
        assert result.contact.email == OWN

    async def test_call_brackets_are_not_part_of_the_address(self) -> None:
        """Непарная скобка в хвосте — конец вызова, а «(at)» внутри — обфускация."""
        body = (
            f'<span data-go="open(mailto:{OWN})">Mail</span>'
            '<i data-x="mailto:sales(at)baeckerei-sample(dot)example(dot)test">x</i>'
        )
        result = await _home(HOST, body)

        assert {candidate.email for candidate in result.candidates} == {
            OWN,
            f"sales@{HOST}",
        }

    async def test_mailto_in_a_head_script_is_a_guess_on_the_own_domain(self) -> None:
        head = f"<script>function mail() {{ window.location = 'mailto:{OWN}'; }}</script>"
        result = await _home(HOST, "<p>Bäckerei</p>", head=head)

        assert result.contact is not None
        assert result.contact.email == OWN
        assert result.contact.guessed

    async def test_foreign_mailto_in_a_head_script_is_not_taken(self) -> None:
        """В скрипте живёт чужой код — виджет со своим ящиком поддержки адресом сайта не
        становится."""
        head = "<script>var help = function() { location = 'mailto:help@widget.example.org'; };</script>"
        result = await _home(HOST, "<p>Bäckerei</p>", head=head)

        assert result.status is ContactStatus.NOT_FOUND

    async def test_bundle_with_many_assignments_is_not_inlined(self) -> None:
        """Сотня присваиваний — бандл, а не сборка адреса: переменные не подставляются."""
        noise = "".join(f'var v{n}="w{n}"; ' for n in range(120))
        head = f'<script>{noise}var u="info"; var d="{HOST}"; x("mailto:"+u+"@"+d);</script>'
        result = await _home(HOST, "<p>Bäckerei</p>", head=head)

        assert result.status is ContactStatus.NOT_FOUND
