"""Извлечение адреса со страницы и фильтр качества.

Проверяется не только «нашёлся адрес», но и то, какой именно из найденных
пойдёт в письмо: на странице их обычно несколько, и выбор между
`info@` и адресом со страницы «advertise» — это выбор между поддержкой
и тем, кто называет цену.
"""

from __future__ import annotations

import pytest
from backend.features.contacts.extract import (
    decode_cloudflare,
    extract_emails,
    extract_obfuscated,
    find_contact_links,
)
from backend.features.contacts.quality import (
    Candidate,
    best,
    rejection_reason,
    trusted_guess,
    weight,
)
from backend.features.core.domain import ContactSource, PageKind


def _cf_encode(email: str, key: int = 0x2A) -> str:
    """Зашифровать адрес так, как это делает Cloudflare, — для проверки
    раскодирования вторым способом, а не сверкой с заранее записанной строкой."""
    return f"{key:02x}" + "".join(f"{ord(ch) ^ key:02x}" for ch in email)


class TestExtract:
    def test_mailto(self) -> None:
        html = '<a href="mailto:Editor@Site.com?subject=hi">пишите</a>'
        assert extract_emails(html) == {"editor@site.com"}

    def test_mailto_two_addresses_in_one_link(self) -> None:
        html = '<a href="mailto:a@site.com,b@site.com">оба</a>'
        assert extract_emails(html) == {"a@site.com", "b@site.com"}

    def test_plain_text(self) -> None:
        html = "<p>Пишите на ads@site.com по вопросам размещения.</p>"
        assert extract_emails(html) == {"ads@site.com"}

    def test_cloudflare_attribute(self) -> None:
        html = f'<a href="/cdn-cgi/l/email-protection" data-cfemail="{_cf_encode("ads@site.com")}">почта</a>'
        assert extract_emails(html) == {"ads@site.com"}

    def test_cloudflare_in_href(self) -> None:
        html = f'<a href="/cdn-cgi/l/email-protection#{_cf_encode("editor@site.com")}">почта</a>'
        assert extract_emails(html) == {"editor@site.com"}

    def test_broken_cloudflare_string_is_not_a_crash(self) -> None:
        """Мусорный атрибут — не поломка страницы: адреса просто нет."""
        assert decode_cloudflare("не-шестнадцатеричное") == ""
        assert decode_cloudflare("ff") == ""

    def test_obfuscation_is_not_a_direct_address(self) -> None:
        """Угаданный адрес приходит отдельным списком: доверяют ему не всегда."""
        html = "<p>write to info [at] site [dot] com</p>"
        assert extract_emails(html) == set()
        assert extract_obfuscated(html) == {"info@site.com"}

    def test_html_entity(self) -> None:
        html = "<p>info&#64;site.com</p>"
        assert "info@site.com" in extract_emails(html)

    def test_empty_page_is_legal(self) -> None:
        assert extract_emails("") == set()
        assert extract_emails("<html><body>нет адреса</body></html>") == set()

    def test_ordinary_text_turns_into_a_plausible_address(self) -> None:
        """Цена приёма: обычная фраза после замены выглядит как настоящий
        адрес, и фильтру качества придраться не к чему. Отсекает её не
        фильтр, а правило доверия — оно требует домен сайта."""
        assert extract_obfuscated("<p>meet at the dot com</p>") == {"meet@the.com"}
        assert rejection_reason("meet@the.com") is None
        assert not trusted_guess("meet@the.com", site_host="site.com")
        assert trusted_guess("info@site.com", site_host="site.com")
        assert trusted_guess("editor@gmail.com", site_host="site.com")


class TestContactLinks:
    def test_by_href(self) -> None:
        html = '<a href="/write-for-us/">пишите нам</a><a href="/news">новости</a>'
        assert find_contact_links(html, slugs=frozenset({"write-for-us"})) == {"/write-for-us/"}

    def test_by_anchor_text_when_slug_is_unguessable(self) -> None:
        """Раздел лежит по адресу /p/12345, и угадать его по слагу нельзя —
        зато анкор называет вещи своими именами."""
        html = '<a href="/p/12345">Advertise with us</a>'
        assert find_contact_links(html, slugs=frozenset({"advertise"})) == {"/p/12345"}

    def test_service_links_are_skipped(self) -> None:
        html = '<a href="mailto:a@b.com">contact</a><a href="#contact">contact</a>'
        assert find_contact_links(html, slugs=frozenset({"contact"})) == set()


class TestRejection:
    @pytest.mark.parametrize(
        ("email", "marker"),
        [
            ("noreply@site.com", "не принимает ответов"),
            ("your-email@site.com", "заглушка вместо адреса"),
            ("hello@example.com", "домен сервиса"),
            ("owner@whoisguard.com", "регистратор"),
            ("logo@site.com.png", "хвост имени файла"),
            ("совсем не адрес", "не похож на адрес"),
        ],
    )
    def test_named_reason(self, email: str, marker: str) -> None:
        """Причина называет правило: «отсеян фильтром» не даёт его настроить."""
        reason = rejection_reason(email)
        assert reason is not None
        assert marker in reason

    @pytest.mark.parametrize(
        "email",
        [
            "info@site.com",
            "ads@site.com",
            "vitaly@site.com",
            "webmaster@gmail.com",
            # Издание о приватности — годный донор: сверяются метки домена,
            # а не подстроки.
            "editor@privacyinternational.org",
        ],
    )
    def test_usable_addresses_pass(self, email: str) -> None:
        assert rejection_reason(email) is None

    @pytest.mark.parametrize(
        ("email", "marker"),
        [
            ("xxx@xxx.xxx", "заглушка"),
            ("aaa@site.com", "заглушка"),
            ("optout@experian.com", "чужой отдел"),
            ("unsubscribe@site.com", "чужой отдел"),
            ("abuse@site.com", "чужой отдел"),
            ("careers@site.com", "чужой отдел"),
            ("legal@site.com", "чужой отдел"),
            ("online@consumerprivacy.experian.com", "поддоменом"),
            ("hello@privacy.site.com", "поддоменом"),
        ],
    )
    def test_found_by_the_live_run(self, email: str, marker: str) -> None:
        """Оба случая пришли с боевого прогона на 80 доменах: заглушку
        `xxx@xxx.xxx` фильтр пропустил как обычный адрес, а адрес отписки —
        как рабочий контакт. Письмо в отписку — это не только бесполезно,
        это заявка на жалобу."""
        reason = rejection_reason(email)
        assert reason is not None
        assert marker in reason


class TestWeight:
    def test_money_page_beats_home(self) -> None:
        """Адрес со страницы «advertise» ведёт к тому, кто называет цену,
        а общий info@ с главной — в поддержку."""
        ads = Candidate("ads@site.com", ContactSource.PAGE, PageKind.MONEY)
        info = Candidate("info@site.com", ContactSource.PAGE, PageKind.HOME)
        assert best([info, ads], site_host="site.com") is ads

    def test_own_domain_beats_free_mailbox(self) -> None:
        own = Candidate("editor@site.com", ContactSource.PAGE, PageKind.CONTACT)
        free = Candidate("editor@gmail.com", ContactSource.PAGE, PageKind.CONTACT)
        assert best([free, own], site_host="site.com") is own

    def test_contact_page_beats_about(self) -> None:
        contact = Candidate("a@site.com", ContactSource.PAGE, PageKind.CONTACT)
        about = Candidate("b@site.com", ContactSource.PAGE, PageKind.ABOUT)
        assert best([about, contact], site_host="site.com") is contact

    def test_provider_confidence_counts(self) -> None:
        sure = Candidate("a@site.com", ContactSource.PROVIDER, confidence=95)
        unsure = Candidate("b@site.com", ContactSource.PROVIDER, confidence=70)
        assert weight(sure, site_host="site.com") > weight(unsure, site_host="site.com")

    def test_nothing_found_is_legal(self) -> None:
        assert best([], site_host="site.com") is None
