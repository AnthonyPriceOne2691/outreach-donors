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
