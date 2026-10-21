"""Домены конкурентов из формы — к одной строгой форме до первого юнита (`competitor_hosts`).

Строка поля уходит в Ahrefs целью запроса, и всё, что ушло, оплачено: отказ — до сети и
словами. Домены — RFC 2606 (`example.com`, `.net`, `.org`); национальное имя — то, что
назвало ревью; платформы — те, что знает гейт исключений (`runs/exclusions.by_name`).
"""

from __future__ import annotations

import pytest
from backend.features.runs.competitor_hosts import MAX_COMPETITORS, ascii_host, competitor_hosts

#: Пример из ревью: национальное имя и его ASCII-форма IDNA — один конкурент.
UNICODE_NAME = "пример.рф"
PUNYCODE_NAME = "xn--e1afmkfd.xn--p1ai"


class TestForm:
    def test_typed_domains_become_root_domains_without_repeats(self) -> None:
        typed = ["https://www.Example.com/path?x=1", "blog.example.com", " example.net ", ""]

        assert competitor_hosts(typed) == ["example.com", "example.net"]

    def test_port_trailing_dot_and_scheme_are_not_part_of_the_name(self) -> None:
        typed = ["EXAMPLE.COM.", "example.com:443", "ftp://example.net/path?q=1#x"]

        assert competitor_hosts(typed) == ["example.com", "example.net"]

    def test_only_blank_lines_are_refused(self) -> None:
        with pytest.raises(ValueError, match="Конкурентов нет"):
            competitor_hosts(["", "   "])

    def test_more_than_the_ceiling_is_refused_with_the_count(self) -> None:
        typed = [f"rival{index}.github.io" for index in range(MAX_COMPETITORS + 1)]

        with pytest.raises(ValueError, match=f"не больше {MAX_COMPETITORS}, пришло 6"):
            competitor_hosts(typed)


class TestStrictNames:
    """Ahrefs не знает имени — минимум за запрос он всё равно возьмёт: отказ до сети."""

    @pytest.mark.parametrize(
        "typed",
        ["a_b.com", "-bad-.com", "bad-.example.com", "*.example.com", f"{'a' * 64}.com"],
    )
    def test_name_outside_dns_rules_is_refused(self, typed: str) -> None:
        with pytest.raises(ValueError, match="не домен по правилам имён"):
            competitor_hosts([typed])

    def test_whole_name_longer_than_dns_allows_is_refused(self) -> None:
        typed = ".".join(["a" * 60] * 5) + ".com"

        with pytest.raises(ValueError, match="имя — до 253"):
            competitor_hosts([typed])

    @pytest.mark.parametrize(
        "typed", ["exa mple.com", "exa\u200bmple.com", "example.com\u00ad", "\u202eexample.com"]
    )
    def test_space_or_invisible_sign_is_refused(self, typed: str) -> None:
        """Невидимый знак прячет в имени то, чего человек не видит: список ушёл бы не туда."""
        with pytest.raises(ValueError, match="пробел или невидимый знак"):
            competitor_hosts([typed])

    @pytest.mark.parametrize(
        "typed",
        [
            "%65xample.com",
            "user:pass@example.com",
            "http://example.com@example.net",
            "example.com\\@example.net",
            "mailto:a@example.com",
        ],
    )
    def test_address_that_leads_elsewhere_is_refused(self, typed: str) -> None:
        """`%65` — это «e» кодом, `@` уводит на второе имя: адрес значит не то, что видно."""
        with pytest.raises(ValueError, match="ведёт не туда, куда виден"):
            competitor_hosts([typed])

    @pytest.mark.parametrize("typed", ["1.2.3.4", "localhost:8000", "javascript:alert(1)"])
    def test_address_without_a_public_domain_is_refused(self, typed: str) -> None:
        with pytest.raises(ValueError, match=r"— не домен\. Впишите домен конкурента"):
            competitor_hosts([typed])

    def test_path_may_carry_at_sign_but_the_name_may_not(self) -> None:
        """`@` в пути законен, в имени — нет: решает часть до пути."""
        assert competitor_hosts(["example.com/@team"]) == ["example.com"]


class TestMixedScripts:
    """Метка в двух письменностях — подделка адреса: список её пуст, а запрос оплачен."""

    @pytest.mark.parametrize("typed", ["ex\u0430mple.com", "xn--exmple-4nf.com"])
    def test_latin_with_a_cyrillic_letter_is_refused(self, typed: str) -> None:
        with pytest.raises(
            ValueError, match=r"смешаны письменности \(кириллица и латиница\)"
        ) as caught:
            competitor_hosts([typed])

        assert "подделка адреса" in str(caught.value)

    @pytest.mark.parametrize(
        ("typed", "host"),
        [
            ("\u30c9\u30e1\u30a4\u30f3\u540d\u4f8b.jp", "xn--eckwd4c7cu47r2wf.jp"),
            (
                "\u03c0\u03b1\u03c1\u03ac\u03b4\u03b5\u03b9\u03b3\u03bc\u03b1.gr",
                "xn--hxajbheg2az3al.gr",
            ),
        ],
    )
    def test_one_script_or_kana_with_ideographs_is_a_name(self, typed: str, host: str) -> None:
        """Японская метка смешивает иероглифы и кану законно; греческая — одна письменность."""
        assert competitor_hosts([typed]) == [host]

    @pytest.mark.parametrize(
        ("typed", "host"),
        [
            # знаки повтора и долготы — часть иероглифов и каны, а не своя письменность
            ("佐々木.jp", "xn--u6jy55gilq.jp"),
            ("〆切.jp", "xn--v6jt73h.jp"),
            ("ラーメン.jp", "xn--4dkp5a8a.jp"),
            # латиница с восточноазиатским письмом и иероглифы с хангылем и бопомофо
            ("ドメインname.jp", "xn--name-uk4cni7fuf.jp"),
            ("例example.cn", "xn--example-ff6k.cn"),
            ("도메인example.kr", "xn--example-vy31a954bf54a.kr"),
            ("韓國도메인.kr", "xn--9cs902mfsr95fuvr.kr"),
            ("ㄅㄆ例.tw", "xn--5ekc355u.tw"),
        ],
    )
    def test_east_asian_mix_of_the_highly_restrictive_profile_is_a_name(
        self, typed: str, host: str
    ) -> None:
        """UTS #39, Highly Restrictive: латиница сходится в метке с иероглифами и каной,
        с иероглифами и бопомофо, с иероглифами и хангылем — так пишут японские, китайские и
        корейские имена (ревью 20.10.2026: `佐々木.jp` и `百度baidu.cn` получали отказ)."""
        assert competitor_hosts([typed]) == [host]

    @pytest.mark.parametrize(
        ("typed", "words"),
        [
            ("도メイン.jp", "кана и хангыль"),
            ("пример例.com", "иероглифы и кириллица"),
            ("exαmple.com", "греческое письмо и латиница"),
        ],
    )
    def test_mix_outside_the_profile_is_refused(self, typed: str, words: str) -> None:
        """Хангыль с каной, кириллица с иероглифами, греческая буква в латинице — ни одно
        письмо так не пишет: отказ, и в нём названы обе письменности."""
        with pytest.raises(ValueError, match=f"смешаны письменности \\({words}\\)"):
            competitor_hosts([typed])


class TestNationalNames:
    def test_unicode_and_ascii_forms_are_one_competitor_and_one_list(self) -> None:
        typed = [UNICODE_NAME, PUNYCODE_NAME, f"https://www.{UNICODE_NAME.upper()}/путь"]

        assert competitor_hosts(typed) == [PUNYCODE_NAME]

    def test_ascii_form_is_what_list_rows_are_compared_with(self) -> None:
        assert ascii_host(UNICODE_NAME) == PUNYCODE_NAME
        assert ascii_host("example.com") == "example.com"
        assert ascii_host("bad_name") == "bad_name", "не переводится — как есть"


class TestPlatforms:
    """Список ссылающихся на платформу — «весь интернет», а не площадки ниши."""

    @pytest.mark.parametrize(
        ("typed", "named"),
        [
            ("facebook.com/somepage", "«facebook.com» — платформа или соцсеть"),
            ("youtube.com/@chan", "«youtube.com» — платформа или соцсеть"),
            ("t.me/somechan", "«t.me» — платформа или соцсеть"),
            ("someone.wordpress.com", "«wordpress.com» — платформа или соцсеть"),
            ("alpha.github.io", "«alpha.github.io» — платформа или соцсеть"),
            ("en.wikipedia.org", "«wikipedia.org» — платформа или соцсеть"),
            ("example.gov", "«example.gov» — гос. или учебная зона"),
        ],
    )
    def test_platform_is_refused_by_the_rule_of_the_exclusion_gate(
        self, typed: str, named: str
    ) -> None:
        with pytest.raises(ValueError, match="на такие сайты ссылаются все подряд") as caught:
            competitor_hosts(["example.com", typed])

        assert named in str(caught.value)
