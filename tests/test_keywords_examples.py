"""Примеры сайтов: строка поля → имя сайта, или отказ до сети со строкой и словами.

Имена — синтетические (`.example.test` и зоны из IDNA): тест проверяет правило, а не
чьи-то сайты.
"""

from __future__ import annotations

import pytest
from backend.features.keywords.examples import (
    MAX_EXAMPLES,
    ExamplesError,
    read_examples,
    shown_host,
    site_host,
)


class TestSiteHost:
    @pytest.mark.parametrize(
        ("written", "host"),
        [
            ("garden.example.test", "garden.example.test"),
            # Схема, `www.`, путь, запрос, порт и регистр — не часть имени сайта.
            ("HTTPS://WWW.Garden.Example.TEST/blog/post?x=1#top", "garden.example.test"),
            ("garden.example.test:8080/path", "garden.example.test"),
            ("http://user@garden.example.test/", "garden.example.test"),
            ("https://garden.example.test.", "garden.example.test"),
            # Поддомен — свой сайт: блог на поддомене — не главная компании.
            ("blog.shop.example.test", "blog.shop.example.test"),
        ],
    )
    def test_the_site_is_what_was_pointed_at(self, written: str, host: str) -> None:
        assert site_host(written) == host

    @pytest.mark.parametrize(
        ("written", "host", "shown"),
        [
            ("пример.рф", "xn--e1afmkfd.xn--p1ai", "пример.рф"),
            ("https://сад.example.test/статьи", "xn--80ai1b.example.test", "сад.example.test"),
            ("例え.テスト", "xn--r8jz45g.xn--zckzah", "例え.テスト"),
            ("موقع.مثال", "xn--4gbrim.xn--mgbh0fb", "موقع.مثال"),
            # IDNA 2008: «ß» — своя буква, а не «ss»: `strasse` — другой сайт.
            ("straße.example.test", "xn--strae-oqa.example.test", "straße.example.test"),
        ],
    )
    def test_any_script_goes_out_as_ascii_and_is_shown_as_written(
        self, written: str, host: str, shown: str
    ) -> None:
        """Защита исходящих и запрос получают ASCII, человек — письменность сайта."""
        assert site_host(written) == host
        assert shown_host(host) == shown

    @pytest.mark.parametrize(
        ("written", "why"),
        [
            ("192.168.0.10", "адрес IP"),
            ("http://[::1]/", "адрес IP"),
            ("localhost", "нет зоны"),
            ("garden", "нет зоны"),
            ("ftp://garden.example.test", "схема «ftp»"),
            ("garden.example.local", "служебное имя"),
            ("metadata.google.internal", "служебное имя"),
            ("garden .example.test", "пробел"),
            ("-garden.example.test", "недопустимые знаки"),
            ("garden..example.test", "недопустимые знаки"),
            ("garden.example.123", "зона «123»"),
            ("https://", "нет имени сайта"),
        ],
    )
    def test_not_a_site_is_named_before_any_request(self, written: str, why: str) -> None:
        with pytest.raises(ValueError, match=why):
            site_host(written)


class TestReadExamples:
    def test_lines_keep_their_numbers_and_repeats_are_counted(self) -> None:
        """Человек ищет «строку 4» глазами в своём поле: пустые строки — тоже строки.
        Повтор — не отказ: тот же сайт второй раз не открываем."""
        examples = read_examples(
            ["garden.example.test", "", "https://www.garden.example.test/blog", "news.example.test"]
        )

        assert [(site.line, site.host) for site in examples.sites] == [
            (1, "garden.example.test"),
            (4, "news.example.test"),
        ]
        assert examples.repeats == 1

    def test_every_bad_line_is_named_with_its_number(self) -> None:
        with pytest.raises(ExamplesError) as caught:
            read_examples(["garden.example.test", "", "не сайт", "10.0.0.1"])

        said = str(caught.value)
        assert "строка 3: «не сайт» — пробел" in said
        assert "строка 4: «10.0.0.1» — это адрес IP" in said

    def test_no_examples_is_a_refusal_not_an_empty_build(self) -> None:
        with pytest.raises(ExamplesError, match="Примеров нет"):
            read_examples(["", "   "])

    def test_more_than_the_ceiling_is_refused_with_the_count(self) -> None:
        lines = [f"site{number}.example.test" for number in range(MAX_EXAMPLES + 2)]

        with pytest.raises(ExamplesError, match=f"Примеров {MAX_EXAMPLES + 2} — берём до 10"):
            read_examples(lines)

    def test_repeats_do_not_count_against_the_ceiling(self) -> None:
        """Потолок — сайтов, а не строк: десять сайтов с повторами — десять."""
        lines = [f"site{number}.example.test" for number in range(MAX_EXAMPLES)]

        examples = read_examples([*lines, *lines])

        assert len(examples.sites) == MAX_EXAMPLES
        assert examples.repeats == MAX_EXAMPLES
