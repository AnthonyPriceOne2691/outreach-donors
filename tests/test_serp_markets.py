"""Рынок прогона: язык выдачи и языки генерации.

Проверяется главное решение модуля — **умолчания на английский нет**.
Одиннадцать стран из пятидесяти пяти жили с ним: прогон по Австрии
искал английскую выдачу по английским ключам и отчитывался успехом.
"""

from __future__ import annotations

import pytest
from backend.features.serp import markets
from backend.features.serp.dataforseo import COUNTRY_CODES


class TestEveryMarketHasALanguage:
    def test_no_country_is_left_without_one(self) -> None:
        """Страна в карте кодов без языка означала бы тихий английский —
        и означала одиннадцать раз."""
        missing = sorted(set(COUNTRY_CODES) - set(markets.SERP_LANGUAGE))

        assert missing == [], f"без языка остались: {missing}"

    def test_no_language_without_a_country(self) -> None:
        """Обратная сторона: язык для страны, которую прогон не умеет,
        читается как поддержка рынка, которой нет."""
        extra = sorted(set(markets.SERP_LANGUAGE) - set(COUNTRY_CODES))

        assert extra == [], f"язык есть, а страны в кодах нет: {extra}"

    def test_every_code_has_a_name_for_the_prompt(self) -> None:
        """Модель понимает «Bengali» надёжнее сырого «bn» — по коду пул
        выходит пустым. Значит имя обязано быть у каждого языка."""
        codes = set(markets.SERP_LANGUAGE.values())
        codes |= {code for extra in markets.EXTRA_KEYGEN_LANGUAGES.values() for code in extra}
        nameless = sorted(codes - set(markets.LANGUAGE_NAMES))

        assert nameless == [], f"без названия: {nameless}"

    def test_extras_belong_to_known_countries(self) -> None:
        strangers = sorted(set(markets.EXTRA_KEYGEN_LANGUAGES) - set(COUNTRY_CODES))

        assert strangers == []


class TestUnknownMarketIsLoud:
    def test_unknown_country_refuses_instead_of_english(self) -> None:
        """Английский по умолчанию уводит прогон в другой веб, и уводит
        молча: отчёт при этом показывает успех."""
        with pytest.raises(markets.UnknownMarketError, match="карте рынков"):
            markets.serp_language("zz")

    def test_refusal_says_what_to_do(self) -> None:
        with pytest.raises(markets.UnknownMarketError) as failure:
            markets.keygen_languages("zz")

        assert "SERP_LANGUAGE" in str(failure.value)


class TestLanguagesOfAMarket:
    @pytest.mark.parametrize(
        ("country", "expected"),
        [
            ("us", ("English",)),
            ("at", ("German",)),  # было «English» по умолчанию
            ("sa", ("Arabic",)),  # было «English» по умолчанию
            ("ca", ("English", "French")),  # Квебек — своя пресса
            ("ua", ("Ukrainian", "Russian")),
            ("ph", ("English", "Filipino")),  # код `fil`, а не `tl`
        ],
    )
    def test_market_languages(self, country: str, expected: tuple[str, ...]) -> None:
        assert markets.keygen_languages(country) == expected

    def test_serp_language_is_the_first_of_them(self) -> None:
        """Выдаче уходит один язык, и это основной язык рынка: замер
        показал, что Google судит больше по самому запросу — восемь
        общих доменов из девяти при разных кодах языка."""
        for country in markets.SERP_LANGUAGE:
            first = markets.keygen_languages(country)[0]
            assert first == markets.LANGUAGE_NAMES[markets.SERP_LANGUAGE[country]]

    def test_extras_are_few_on_purpose(self) -> None:
        """Язык, который и так покрыт основным, удваивает вызовы модели
        и ничего не приносит. Список короткий намеренно, и рост этого
        числа должен быть осознанным."""
        assert len(markets.EXTRA_KEYGEN_LANGUAGES) <= 8
