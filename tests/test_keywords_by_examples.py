"""Сборка по примерам целиком: главные → тема → тот же генератор, и порядок отказов.

Модель и сеть подделаны: модель отвечает о теме объектом и фразами на просьбы углов,
главные — готовыми страницами. Проверяется проводка: генератор получает тему примеров
и тему ниши на языке каждого языка рынка, а отказы, которые не ждут модели, звучат
до главных и до первого токена.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from backend.features.keywords.angles import UnknownPresetError, load_prompt, preset
from backend.features.keywords.by_examples import NothingReadError, build_from_examples
from backend.features.keywords.client import Ask
from backend.features.keywords.example_pages import ExamplePage
from backend.features.keywords.example_theme import THEME_PROMPT
from backend.features.keywords.examples import Example, ExamplesError
from backend.features.keywords.footprints import NoFootprintsError
from backend.features.keywords.generator import PoolBuilder, build_user_prompt
from backend.features.keywords.hygiene import parse_phrases


class FakeModel:
    """Модель-заглушка: о теме — объект, на просьбы углов — фразы по очереди."""

    def __init__(self, theme: dict[str, Any] | None, phrases: list[list[str]] | None = None):
        self.theme = theme
        self.phrases = list(phrases or [])
        self.asks: list[Ask] = []
        self.tokens_spent = 0
        self.calls = 0
        self.refusals: list[str] = []

    async def reply(self, ask: Ask) -> str | None:
        self.asks.append(ask)
        self.calls += 1
        self.tokens_spent += 500
        if ask.system == load_prompt(THEME_PROMPT):
            if self.theme is None:
                self.refusals.append("модель, отказ (чинить): HTTP 401: ключ не принят")
                return None
            return json.dumps(self.theme, ensure_ascii=False)
        return json.dumps(self.phrases.pop(0) if self.phrases else [], ensure_ascii=False)

    async def ask(self, ask: Ask) -> list[str]:
        content = await self.reply(ask)
        return parse_phrases(content) if content else []

    def prompts(self) -> list[str]:
        """Просьбы генератора — без просьбы о теме."""
        return [ask.user for ask in self.asks if ask.system != load_prompt(THEME_PROMPT)]


class FakeReader:
    """Главные готовыми страницами; прочие примеры — «не ответил»."""

    def __init__(self, pages: dict[str, ExamplePage]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    async def read_all(self, examples: Sequence[Example]) -> list[ExamplePage]:
        self.asked.extend(example.host for example in examples)
        return [
            self.pages.get(example.host, ExamplePage(example, reason="не ответил вовремя"))
            for example in examples
        ]


def page(host: str, title: str, language: str) -> ExamplePage:
    return ExamplePage(
        Example(1, host), url=f"https://{host}/", title=title, text=title, language=language
    )


GARDEN = page("garden.example.test", "Gartenfreunde — Tipps für Garten", "de")
BALCONY = page("balcony.example.test", "Balcony gardening for small flats", "en")
THEME = {
    "theme": "блоги о домашнем садоводстве",
    "about": "Hobby gardening blogs with practical advice for home gardeners.",
    "topics": {"English": "home gardening", "French": "jardinage", "German": "gartenarbeit"},
    "off_theme": [],
}


def words(prefix: str, count: int) -> list[str]:
    """Заведомо непохожие фразы: дедуп их не тронет (`test_keywords_generator._phrases`)."""
    return [f"{prefix}{number}альфа {prefix}{number}бета" for number in range(count)]


async def build(
    model: FakeModel,
    reader: FakeReader,
    lines: Sequence[str],
    *,
    country: str = "us",
    languages: Sequence[str] = ("English",),
    preset_name: str = "reviews",
    cap: int = 6,
) -> Any:
    return await build_from_examples(
        model,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        lines,
        country=country,
        languages=languages,
        preset_name=preset_name,
        cap=cap,
    )


class TestPromptWithExamples:
    def test_examples_say_what_sites_we_look_for_not_their_names(self) -> None:
        angle = preset("reviews")[0]
        prompt = build_user_prompt(
            country="us",
            language="English",
            angle=angle,
            topic="home gardening",
            examples="Hobby gardening blogs for home gardeners.",
        )

        assert "sites we look for: Hobby gardening blogs for home gardeners." in prompt
        assert "MORE sites of this kind, not one particular site" in prompt
        assert "topic: EVERY query must be about home gardening" in prompt

    def test_without_examples_the_prompt_is_as_before(self) -> None:
        """Сборка по теме не меняется: строки о примерах в ней нет."""
        prompt = build_user_prompt(country="us", language="English", angle=preset("reviews")[0])

        assert "sites we look for" not in prompt


class TestBuild:
    async def test_the_generator_gets_the_theme_of_the_examples(self) -> None:
        model = FakeModel(THEME, [words("a", 9), words("b", 9), words("c", 9)])
        made = await build(
            model, FakeReader({"garden.example.test": GARDEN}), ["garden.example.test"]
        )

        assert made.theme.theme == "блоги о домашнем садоводстве"
        assert len(made.pool.keywords) == 6
        prompts = model.prompts()
        assert prompts, "генератор не спросили"
        assert all("sites we look for: Hobby gardening blogs" in prompt for prompt in prompts)
        assert all("must be about home gardening" in prompt for prompt in prompts)

    async def test_each_market_language_gets_its_own_topic(self) -> None:
        """Тема на чужом языке рядом со словами футпринта дала бы запрос на двух
        языках: в двуязычном рынке у каждого языка — своя тема."""
        model = FakeModel(THEME, [words(f"r{number}", 9) for number in range(12)])
        await build(
            model,
            FakeReader({"garden.example.test": GARDEN}),
            ["garden.example.test"],
            country="ca",
            languages=("English", "French"),
            cap=12,
        )

        french = [prompt for prompt in model.prompts() if "language: French" in prompt]
        english = [prompt for prompt in model.prompts() if "language: English" in prompt]
        assert french
        assert english
        assert all("must be about jardinage" in prompt for prompt in french)
        assert all("must be about home gardening" in prompt for prompt in english)

    @pytest.mark.parametrize(
        ("country", "language", "first"),
        [
            ("us", "English", "home gardening write for us"),
            ("de", "German", "gartenarbeit gastbeitrag"),
        ],
    )
    async def test_guest_puts_the_niche_of_the_examples_first(
        self, country: str, language: str, first: str
    ) -> None:
        """Набор «guest»: тема ниши — на письме и языке футпринта рынка, и она первая."""
        model = FakeModel(THEME, [["kitchen garden", "raised beds", "lawn care"]])
        made = await build(
            model,
            FakeReader({"garden.example.test": GARDEN}),
            ["garden.example.test"],
            country=country,
            languages=(language,),
            preset_name="guest",
            cap=8,
        )

        assert made.pool.keywords[0] == first

    async def test_the_theme_stands_on_the_pages_that_were_read(self) -> None:
        """Отказ одного примера — не отказ сборки: тема — по прочитанным, а не
        прочитанный назван с причиной."""
        model = FakeModel(THEME, [words("a", 9)] * 3)
        made = await build(
            model,
            FakeReader({"garden.example.test": GARDEN}),
            ["garden.example.test", "gone.example.test"],
        )

        theme_prompt = model.asks[0].user
        assert "garden.example.test" in theme_prompt
        assert "gone.example.test" not in theme_prompt
        assert [(p.example.host, p.reason) for p in made.pages] == [
            ("garden.example.test", ""),
            ("gone.example.test", "не ответил вовремя"),
        ]

    async def test_tokens_of_the_theme_are_counted_apart_and_in_the_total(self) -> None:
        model = FakeModel(THEME, [words("a", 9)] * 3)
        made = await build(
            model, FakeReader({"garden.example.test": GARDEN}), ["garden.example.test"]
        )

        assert made.theme_tokens == 500
        assert made.pool.report.tokens == model.tokens_spent > made.theme_tokens

    async def test_repeated_lines_are_counted_not_read_twice(self) -> None:
        model = FakeModel(THEME, [words("a", 9)] * 3)
        reader = FakeReader({"garden.example.test": GARDEN})
        made = await build(
            model, reader, ["garden.example.test", "https://www.garden.example.test/blog"]
        )

        assert reader.asked == ["garden.example.test"]
        assert made.repeats == 1


class TestRefusalsBeforeSpending:
    async def test_nothing_read_is_a_refusal_without_a_single_token(self) -> None:
        """Тема из пустоты оплатила бы выдачу ни за чем: ни одной главной — отказ,
        и модель не зовётся вовсе."""
        model = FakeModel(THEME)

        with pytest.raises(NothingReadError) as caught:
            await build(model, FakeReader({}), ["gone.example.test", "mute.example.test"])

        assert model.calls == 0
        said = str(caught.value)
        assert "gone.example.test — не ответил вовремя" in said
        assert "соберите ключи по теме" in said

    async def test_bad_lines_refuse_before_any_page(self) -> None:
        reader = FakeReader({})

        with pytest.raises(ExamplesError, match="строка 2"):
            await build(FakeModel(THEME), reader, ["garden.example.test", "не сайт"])

        assert reader.asked == []

    @pytest.mark.parametrize(
        ("country", "languages", "preset_name", "cap", "error"),
        [
            ("us", ("English",), "такого-нет", 6, UnknownPresetError),
            # У японского рынка нет футпринтов набора «guest».
            ("jp", ("Japanese",), "guest", 8, NoFootprintsError),
            # Два языка на шесть ключей — меньше пяти на сочетание.
            ("ca", ("English", "French"), "reviews", 6, ValueError),
        ],
    )
    async def test_refusals_of_the_generator_come_before_the_pages(
        self, country: str, languages: tuple[str, ...], preset_name: str, cap: int, error: type
    ) -> None:
        """Тот же отказ после главных и вызова о теме стоил бы минуты и токенов."""
        reader = FakeReader({"garden.example.test": GARDEN})
        model = FakeModel(THEME)

        with pytest.raises(error):
            await build(
                model,
                reader,
                ["garden.example.test"],
                country=country,
                languages=languages,
                preset_name=preset_name,
                cap=cap,
            )

        assert reader.asked == []
        assert model.calls == 0

    def test_check_is_silent_when_the_build_may_go(self) -> None:
        PoolBuilder(FakeModel(THEME)).check(  # type: ignore[arg-type]
            cap=10, languages=("English", "French"), preset_name="guest"
        )
