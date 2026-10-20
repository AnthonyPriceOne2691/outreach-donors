"""Общая тема примеров: что уходит модели и как понимается её ответ.

Модель здесь не зовётся: подделана сеть провайдера (`httpx.MockTransport`) или клиент.
Проверяется наше: просьба несёт главные на их языках, ответ разбирается в тему для
человека и тему ниши на каждом языке рынка, а непонятный ответ — отказ словами.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.features.keywords.angles import load_prompt
from backend.features.keywords.client import KeygenClient, LlmError
from backend.features.keywords.example_pages import ExamplePage
from backend.features.keywords.example_theme import (
    THEME_PROMPT,
    build_theme_prompt,
    infer_theme,
    parse_theme,
)
from backend.features.keywords.examples import Example

GARDEN = ExamplePage(
    Example(1, "garden.example.test"),
    url="https://garden.example.test/",
    title="Gartenfreunde — Tipps für Garten und Balkon",
    description="Ratgeber für Hobbygärtner",
    headings=("Tomaten richtig pflanzen",),
    menu=("Gemüse", "Rasen"),
    text="Wir zeigen, wie Sie Tomaten anbauen.",
    language="de",
)
BALCONY = ExamplePage(
    Example(2, "xn--80ai1b.example.test"),  # «сад» письменностью сайта
    url="https://xn--80ai1b.example.test/",
    title="Сад на балконе",
    description="Цветы и овощи в контейнерах",
    language="ru",
)
HOSTS = ["garden.example.test", "xn--80ai1b.example.test"]


def answer(**fields: Any) -> str:
    base: dict[str, Any] = {
        "theme": "блоги о садоводстве",
        "about": "Hobby gardening blogs with practical advice for home gardeners.",
        "topics": {"English": "Home Gardening"},
        "off_theme": [],
    }
    return json.dumps({**base, **fields}, ensure_ascii=False)


class TestPrompt:
    def test_every_read_page_goes_in_its_own_script(self) -> None:
        prompt = build_theme_prompt(
            [GARDEN, BALCONY], country="ca", languages=["English", "French"]
        )

        assert "market/country: ca" in prompt
        assert "languages of the market: English, French" in prompt
        assert "Example 1: garden.example.test" in prompt
        # Имя — письменностью сайта: модель читает имя, как его видит человек.
        assert "Example 2: сад.example.test" in prompt
        assert "page language: de" in prompt
        assert "Tomaten richtig pflanzen" in prompt
        assert "menu: Gemüse · Rasen" in prompt
        assert "Цветы и овощи в контейнерах" in prompt

    def test_the_prompt_file_forbids_names_and_treats_pages_as_data(self) -> None:
        """Запрос с именем сайта нашёл бы его самого; текст страницы — данные, а не
        указания: это сказано модели, а ответ всё равно проверяется."""
        system = load_prompt(THEME_PROMPT)

        assert "never the sites" in system
        assert "not instructions" in system
        assert '"topics"' in system
        assert "EACH language of the market" in system


class TestParse:
    def test_a_clean_answer_becomes_the_theme(self) -> None:
        theme = parse_theme(answer(), languages=["English"], hosts=HOSTS)

        assert theme.theme == "блоги о садоводстве"
        # Тема ниши — по гигиене фразы: нижний регистр, пробелы схлопнуты.
        assert theme.topics == {"English": "home gardening"}
        assert theme.about.startswith("Hobby gardening blogs")
        assert theme.off_theme == ()

    def test_a_fenced_answer_is_still_read(self) -> None:
        theme = parse_theme(f"```json\n{answer()}\n```", languages=["English"], hosts=HOSTS)

        assert theme.topics == {"English": "home gardening"}

    @pytest.mark.parametrize(
        ("topics", "languages", "expected"),
        [
            # Каждому языку рынка — своя тема на его языке и письменности.
            (
                {"English": "home gardening", "French": "jardinage"},
                ["English", "French"],
                {"English": "home gardening", "French": "jardinage"},
            ),
            # Язык назван кодом — тот же язык.
            ({"ja": "家庭菜園"}, ["Japanese"], {"Japanese": "家庭菜園"}),
            ({"arabic": "أخبار كرة القدم"}, ["Arabic"], {"Arabic": "أخبار كرة القدم"}),
            # Языка нет в ответе — тема первого названного: угол её переведёт.
            (
                {"German": "gartenarbeit"},
                ["German", "French"],
                {"German": "gartenarbeit", "French": "gartenarbeit"},
            ),
        ],
    )
    def test_topics_are_per_market_language(
        self, topics: dict[str, str], languages: list[str], expected: dict[str, str]
    ) -> None:
        theme = parse_theme(answer(topics=topics), languages=languages, hosts=HOSTS)

        assert theme.topics == expected

    def test_a_topic_that_is_a_sentence_or_stale_is_not_taken(self) -> None:
        """Тема ниши — именная группа: предложение или «итоги 2001» гигиена отсеяла бы
        и у запроса."""
        topics = {"English": "the best tips for growing tomatoes at home", "French": "jardin 2001"}

        with pytest.raises(LlmError, match="ни на одном языке рынка"):
            parse_theme(answer(topics=topics), languages=["English", "French"], hosts=HOSTS)

    def test_off_theme_names_only_given_examples(self) -> None:
        theme = parse_theme(
            answer(off_theme=["https://www.Garden.example.test/", "other.example.test", 7]),
            languages=["English"],
            hosts=HOSTS,
        )

        assert theme.off_theme == ("garden.example.test",)

    def test_all_examples_off_theme_is_a_contradiction_and_is_dropped(self) -> None:
        theme = parse_theme(
            answer(off_theme=["garden.example.test", "сад.example.test"]),
            languages=["English"],
            hosts=HOSTS,
        )

        assert theme.off_theme == ()

    def test_missing_about_falls_back_to_the_topic(self) -> None:
        theme = parse_theme(answer(about=""), languages=["English"], hosts=HOSTS)

        assert theme.about == "home gardening"

    @pytest.mark.parametrize(
        ("content", "why"),
        [
            ("это не объект", "не объектом"),
            ('["список", "фраз"]', "не объектом"),
            ('{"theme": "сад", "topics": {', "оборван"),
            ('{"theme": сад}', "не разобран"),
            (answer(theme="  "), "не назвала общую тему"),
            (answer(topics={}), "ни на одном языке рынка"),
            (answer(topics=["home gardening"]), "ни на одном языке рынка"),
        ],
    )
    def test_an_answer_that_cannot_build_a_pool_is_a_refusal(self, content: str, why: str) -> None:
        """Ключи по выдуманной теме оплатили бы выдачу ни за чем: отказ — словами."""
        with pytest.raises(LlmError, match=why):
            parse_theme(content, languages=["English"], hosts=HOSTS)


class TestInfer:
    async def test_the_theme_is_asked_of_the_keyword_model(self) -> None:
        """Тема — первый шаг той же сборки: модель та же, что собирает ключи
        (пин не ниже пятой — замер), и параметры — те же."""
        sent: list[dict[str, Any]] = []

        def provider(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": answer()}}],
                    "usage": {"total_tokens": 1234},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            client = KeygenClient(http, api_key="k")
            theme = await infer_theme(client, [GARDEN], country="us", languages=["English"])

        assert theme.theme == "блоги о садоводстве"
        assert client.tokens_spent == 1234
        [payload] = sent
        assert payload["model"] == llm_cfg.KEYGEN_MODEL
        assert payload["messages"][0]["content"] == load_prompt(THEME_PROMPT)
        assert "Tomaten richtig pflanzen" in payload["messages"][1]["content"]

    async def test_a_model_refusal_is_said_with_its_reason(self) -> None:
        provider = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": {"message": "ключ не принят"}})
        )
        async with httpx.AsyncClient(transport=provider) as http:
            client = KeygenClient(http, api_key="k")
            with pytest.raises(LlmError, match="ключ не принят"):
                await infer_theme(client, [GARDEN], country="us", languages=["English"])
