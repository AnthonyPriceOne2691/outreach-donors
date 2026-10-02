"""Извлечение адреса со страницы и фильтр качества.

Проверяется не только «нашёлся адрес», но и то, какой именно из найденных
пойдёт в письмо: на странице их обычно несколько, и выбор между
`info@` и адресом со страницы «advertise» — это выбор между поддержкой
и тем, кто называет цену.
"""

from __future__ import annotations

import time

import pytest
from backend.features.contacts.extract import (
    decode_cloudflare,
    extract_emails,
    extract_obfuscated,
    find_contact_links,
    repair_glued_domain,
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


class TestBareAt:
    """Голое «at» — обычное английское слово (ревью e6, 02.10.2026).

    Живьём кодом прода: «hosted here at AdventureAlan.com» на странице
    «о нас» давал here@adventurealan.com, «March 05, 2024 At business.com» —
    2024@business.com. Оба на домене сайта, правилу доверия придраться не
    к чему, и оба выигрывали выбор адреса.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "All of our content will still be hosted here at AdventureAlan.com",
            "Last Updated: March 05, 2024 At business.com, it's our goal",
            "Our team at business.com is dedicated to small owners",
            "The marketing at site.com grew last year",
        ],
    )
    def test_prose_is_not_an_address(self, text: str) -> None:
        assert extract_obfuscated(f"<p>{text}</p>") == set()

    @pytest.mark.parametrize(
        ("text", "email"),
        [
            ("Email: info at site dot com", "info@site.com"),
            ("Editor AT Site DOT com", "editor@site.com"),
            ("Write to ads at site.com for pricing", "ads@site.com"),
            ("info(at)site.com", "info@site.com"),
        ],
    )
    def test_obfuscation_is_still_read(self, text: str, email: str) -> None:
        """Обратная сторона: так прячут адрес те, кто продаёт размещение."""
        assert extract_obfuscated(f"<p>{text}</p>") == {email}

    @pytest.mark.parametrize(
        "text",
        [
            "Get help at site.com",
            "Say hi at site.com",
            "Head of Sales at site.com",
            "Digital marketing at site.com",
            "Social media at site.com",
            "Find more info at site.com",
            "Talk to our customer support team at site.com",
        ],
    )
    def test_roles_that_are_prose_before_at(self, text: str) -> None:
        """Перед голым «at» — только «почтовые» роли: помощь, продажи и соцсети
        в прозе стоят перед «at» постоянно (ревью #137)."""
        assert extract_obfuscated(f"<p>{text}</p>") == set()

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            ("Write to mike.blogger at gmail.com", {"mike.blogger@gmail.com"}),
            ("jane at gmail.com", {"jane@gmail.com"}),
            ("Log in at gmail.com", set()),
            ("Find us at gmail.com", set()),
        ],
    )
    def test_free_mailbox_after_bare_at(self, text: str, found: set[str]) -> None:
        """Так пишут мелкие блоги: «mike at gmail.com». Кроме оборотов вроде
        «Log in at gmail.com», где перед «at» не локальная часть."""
        assert extract_obfuscated(f"<p>{text}</p>") == found


class TestLinearTime:
    """Разбор идёт в цикле событий: одна страница, разбираемая секундами,
    стоит всех параллельных доменов прохода (ревью #137, e6). Потолок щедрый —
    в сотни раз выше нормы, — чтобы тест не падал от нагрузки машины, но
    ловил возврат квадратичного времени (было 33–38 с и 8,5 с)."""

    def test_long_whitespace_run(self) -> None:
        html = "<div>" + "\n" * 40_000 + "info at site dot com</div>"
        started = time.perf_counter()
        assert extract_obfuscated(html) == {"info@site.com"}
        assert time.perf_counter() - started < 1.0

    @pytest.mark.parametrize("token", ["a." * 50_000, "a+" * 50_000, "a-" * 50_000])
    def test_long_dotted_token(self, token: str) -> None:
        """Правило бесплатной почты начиналось на каждой точке внутри токена
        и, не найдя за ним бесплатной почты, пробовало следующую."""
        started = time.perf_counter()
        assert extract_obfuscated(f"<p>{token}</p>") == set()
        assert time.perf_counter() - started < 1.0

    def test_long_token_without_an_address(self) -> None:
        html = f"<body><script>var data = '{'a' * 100_000}';</script><p>ads@site.com</p></body>"
        started = time.perf_counter()
        assert extract_emails(html) == {"ads@site.com"}
        assert time.perf_counter() - started < 1.0


class TestGluedTail:
    """Хвост соседнего слова, слипшийся с зоной адреса.

    Проверяется в обе стороны, и вторая важнее первой: не починить адрес
    значит потерять донора, а обрезать живую зону значит написать не тому,
    кому писали, и не узнать об этом никогда.
    """

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ("<p>Пишите на ads@gmail.comJanuary 5, 2024</p>", "ads@gmail.com"),
            ("<p>ads@site.com2024 год</p>", "ads@site.com"),
            ("<p>ads@gmail.comЯнварь</p>", "ads@gmail.com"),
        ],
    )
    def test_tail_is_cut_off(self, html: str, expected: str) -> None:
        assert extract_emails(html) == {expected}

    @pytest.mark.parametrize(
        "domain",
        ["example.company", "site.network", "x.institute", "y.international", "z.phone"],
    )
    def test_long_zone_survives(self, domain: str) -> None:
        """Список известных зон обрезал бы `company` до `com`, а `institute` до `in`.

        Именно так вела себя рабочая реализация, у которой список был.
        """
        assert repair_glued_domain(domain) == domain

    @pytest.mark.parametrize(
        "domain",
        [
            "site.xn--p1ai",
            "сайт.рф",
            # `xn--d1acj3b` (.дети) — та самая опасная форма: две строчные
            # буквы, сразу за ними цифра. Держит её только привязка поиска
            # к началу метки; снимут привязку — зона обрежется до `.acj`.
            "site.xn--d1acj3b",
        ],
    )
    def test_punycode_zone_survives(self, domain: str) -> None:
        """Единственная зона с цифрами — punycode; по цифре её резать нельзя."""
        assert repair_glued_domain(domain) == domain

    @pytest.mark.parametrize("domain", ["SITE.COM", "Site.Com", "site.com", "site.co.uk"])
    def test_ordinary_domain_is_untouched(self, domain: str) -> None:
        assert repair_glued_domain(domain) == domain

    def test_lowercase_tail_is_left_as_is(self) -> None:
        """Осознанный предел: строчный хвост от долгой зоны не отличить.

        Обрезанный адрес молча уехал бы чужому живому человеку. Не
        починенный, он и не проходит — узкий случай, где зоны заведомо
        нет, отсеивает фильтр годности.
        """
        assert repair_glued_domain("gmail.comand") == "gmail.comand"
        assert rejection_reason("ads@gmail.comand") is not None


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


class TestVendorSubdomains:
    """Домен сервиса на третьем уровне. Найдено боевым прогоном 27.09.2026.

    Ключ телеметрии выглядит как адрес и проходит любую проверку формы:
    списка доменов сервисов недостаточно, если сверять только точное
    совпадение — адрес сидит на поддомене.
    """

    @pytest.mark.parametrize(
        "email",
        [
            "f421b49239504a9a9acbf7335cb6e058@o317978.ingest.sentry.io",
            "79baaa8e09c746d2b7401643b99792e0@sentry.wixpress.com",
            "abc@www.google.com",
        ],
    )
    def test_service_subdomain_is_rejected(self, email: str) -> None:
        assert rejection_reason(email) is not None

    @pytest.mark.parametrize(
        "email",
        [
            # Sentry на СВОЁМ домене площадки: списком доменов не ловится.
            "e7d54b729aaf49ea8b2f80dae22860aa@sentry.zipify.com",
            "73410f1915d84abc8b2dd1f1aabd1c82@sentry.hackmd.dev",
            # И на чужом домене без слова sentry вовсе — остаётся форма ключа.
            "f421b49239504a9a9acbf7335cb6e058@o317978.example-analytics.net",
        ],
    )
    def test_telemetry_key_is_not_a_mailbox(self, email: str) -> None:
        """32 шестнадцатеричных символа в локальной части — это DSN, не ящик.

        Признак по форме, а не по домену: боевой прогон 27.09.2026 отдал три
        варианта подряд, и последний был Sentry на домене самой площадки.
        Сколько доменов в список ни добавь, следующий будет новый.
        """
        assert rejection_reason(email) is not None

    def test_mangled_telemetry_key_is_still_not_a_mailbox(self) -> None:
        """Сломанное JSON-экранирование приклеивает `u003e` к ключу.

        Такой адрес форму ключа уже не проходит — 32 hex перестают быть
        всей локальной частью, — и его ловит только то, что домен начинается
        на `sentry.`. В данных прогона этот мусор встретился живьём
        (`u003epress@hackmd.io` рядом с DSN того же сайта).
        """
        assert (
            rejection_reason("u003ee7d54b729aaf49ea8b2f80dae22860aa@sentry.zipify.com") is not None
        )

    def test_hex_looking_but_short_local_part_survives(self) -> None:
        """Граница: `abc123@site.com` — обычный адрес, а не ключ."""
        assert rejection_reason("abc123@site.com") is None

    def test_own_domain_that_merely_ends_with_a_word_survives(self) -> None:
        """`mysentry.io` — не `sentry.io`: границей служит точка, а не подстрока."""
        assert rejection_reason("ads@mysentry.io") is None

    @pytest.mark.parametrize("email", ["contact@imaginarylane.example", "info@site.invalid"])
    def test_reserved_zone_is_rejected(self, email: str) -> None:
        """RFC 2606 держит эти зоны под примеры — со страниц они и приезжают."""
        assert rejection_reason(email) is not None

    def test_fixture_zone_stays_usable(self) -> None:
        """`.test` тем же RFC зарезервирована, но на ней стоят фикстуры репо.

        Запретив её, мы отняли бы у тестов единственный домен, по которому
        нельзя случайно уйти в живую сеть.
        """
        assert rejection_reason("ads@site.example.test") is None


class TestPlaceholderDomains:
    """Прогон 23.09.2026, 100 ключей US: со страниц снялись адреса из
    примеров — `support@yourcompany.com`, `you@yourbusiness.com`,
    `sarah.mitchell@company.com`. Правило по форме имени домена, а не
    список: заглушек бесконечно много, ниши и страны разные."""

    @pytest.mark.parametrize(
        "email",
        [
            "support@yourcompany.com",
            "you@yourbusiness.com",
            "sarah.mitchell@company.com",
            "info@my-site.co.uk",
            "hello@yourwebsite.de",
            "editor@acme.com",
        ],
    )
    def test_placeholder_domain_is_refused(self, email: str) -> None:
        reason = rejection_reason(email)
        assert reason is not None
        assert "домен-заглушка" in reason

    @pytest.mark.parametrize(
        "email",
        [
            # Настоящее издание из той же выдачи: голый префикс `your`
            # отрезал бы его.
            "info@yourstory.com",
            "info@business.com",
            "press@companyname.io",
            "hello@websitebuilder.com",
            "team@mycompanyhub.com",
        ],
    )
    def test_real_sites_with_similar_names_pass(self, email: str) -> None:
        assert rejection_reason(email) is None


class TestNotTheSitesMailbox:
    """Прогон 27.09.2026 по 14 тыс. площадок: адреса, правдоподобные на вид и
    даже подтверждённые проверкой ящика, но принадлежащие не сайту — хостеру
    на странице-заглушке, платформе блогов в подвале публикации, юрфирме,
    которая представляет платформу в ЕС. Писать туда о размещении бессмысленно."""

    @pytest.mark.parametrize(
        ("email", "marker"),
        [
            ("support@beget.com", "хостера"),
            ("bills@beget.com", "хостера"),
            ("info@hostinger.com", "хостера"),
            ("support@ovh.com", "хостера"),
            ("data-protection@hetzner.com", "хостера"),
            ("support@substack.com", "платформы, а не автора"),
            ("dsa@substackinc.com", "домен сервиса"),
            ("eurepresentative.substack@twobirds.com", "представитель"),
            ("ukrepresentative.substack@twobirds.com", "представитель"),
            ("substack-dsa@lionheartsquared.eu", "представитель"),
            ("mailaddress@client.com", "домен-заглушка"),
            ("info@thenationnetwork.comif", "слипшимся хвостом"),
            ("info@longlead.comor", "слипшимся хвостом"),
        ],
    )
    def test_refused_with_its_reason(self, email: str, marker: str) -> None:
        reason = rejection_reason(email)
        assert reason is not None
        assert marker in reason

    @pytest.mark.parametrize(
        "email",
        [
            # Автор на платформе — законный адрес: ящик платформы отличается формой.
            "wethefifth@substack.com",
            # `ovh` подстрокой в чужом имени — не хостер.
            "info@lovhouse.com",
            # Представитель по продажам — тот, кто нам и нужен.
            "salesrepresentative@site.com",
            # Долгие живые зоны, которые начинаются на com/net/org.
            "hello@site.company",
            "news@site.community",
            "contact@north-star.network",
            "info@thenationnetwork.com",
            "editor@site.com.au",
            "info@clientearth.org",
        ],
    )
    def test_similar_real_addresses_pass(self, email: str) -> None:
        assert rejection_reason(email) is None


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

    def test_a_direct_address_beats_any_guess(self) -> None:
        """Догадка из обфускации — только когда прямого адреса нет: с весом
        за домен сайта ролевая догадка обгоняла настоящий ящик со страницы."""
        guess = Candidate("info@site.com", ContactSource.PAGE, PageKind.MONEY, guessed=True)
        direct = Candidate("owner@gmail.com", ContactSource.PAGE, PageKind.HOME)
        assert best([guess, direct], site_host="site.com") is direct
        assert best([guess], site_host="site.com") is guess
