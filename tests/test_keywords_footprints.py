"""Набор «guest»: темы ниши у модели × футпринты языка рынка по таблице.

Настоящая модель не зовётся: проверяются наши решения — что модель даёт
только темы, что почти-дубли убираются среди тем, а не среди запросов,
и что рынок без футпринтов отказывает до траты.
"""

from __future__ import annotations

import pytest
from backend.config import llm as llm_cfg
from backend.features.keywords.angles import PRESETS, load_prompt
from backend.features.keywords.dedup import drop_near_duplicates
from backend.features.keywords.footprints import (
    FOOTPRINTS,
    NoFootprintsError,
    expand,
    templates_for,
    topics_needed,
)
from backend.features.keywords.generator import PoolBuilder
from tests.test_keywords_generator import FakeClient


class TestTable:
    def test_guest_is_a_preset(self) -> None:
        assert "guest" in PRESETS

    def test_every_template_takes_the_topic_once(self) -> None:
        for language, templates in FOOTPRINTS.items():
            assert len(templates) >= 3, language
            for template in templates:
                assert template.count("{topic}") == 1, (language, template)

    def test_a_language_without_footprints_names_the_known_ones(self) -> None:
        with pytest.raises(NoFootprintsError, match="English, French, German"):
            templates_for("Ukrainian")

    def test_a_topic_goes_through_every_template_before_the_next(self) -> None:
        """Обрезка потолком отнимает последнюю тему, а не последний шаблон
        у всех: шаблоны ищут разное."""
        keys = expand(["diy", "gardening"], ("{topic} write for us", "{topic} guest post"))

        assert keys == [
            "diy write for us",
            "diy guest post",
            "gardening write for us",
            "gardening guest post",
        ]

    def test_word_dedup_would_collapse_one_template_of_two_topics(self) -> None:
        """Почему почти-дубли ищутся среди тем: у запросов одного шаблона
        3 общих слова из 5 — ровно порог дедупа."""
        assert drop_near_duplicates(["diy write for us", "gardening write for us"]) == [
            "diy write for us"
        ]

    def test_topics_needed_covers_the_share(self) -> None:
        assert topics_needed(10, ("a", "b", "c", "d")) == 3
        assert topics_needed(8, ("a", "b", "c", "d")) == 2
        assert topics_needed(0, ("a",)) == 0

    def test_the_prompt_asks_for_topics_and_keeps_the_footprint_to_us(self) -> None:
        prompt = load_prompt("topics")
        assert "{max_phrases}" in prompt
        assert "guest posts" in prompt, "модель не должна дописывать футпринт сама"


class TestGuestPool:
    async def test_model_gives_topics_and_the_table_gives_footprints(self) -> None:
        client = FakeClient([["diy", "gardening", "home decor"]])

        pool = await PoolBuilder(client).build(
            cap=8, country="us", languages=("English",), preset_name="guest"
        )

        assert pool.keywords == [
            "diy write for us",
            "diy guest post",
            "diy submit a guest post",
            "diy sponsored post",
            "gardening write for us",
            "gardening guest post",
            "gardening submit a guest post",
            "gardening sponsored post",
        ]
        assert client.calls == 1
        # Две темы нужны на потолок, у модели просим с запасом под дедуп.
        assert client.asked[0] >= 2 + llm_cfg.ANGLE_BUFFER

    async def test_the_operators_niche_goes_first(self) -> None:
        """«home improvement write for us» — самый широкий запрос ниши,
        а модель отдаёт подтемы. Повтор ниши у модели схлопывается."""
        client = FakeClient([["kitchen remodel", "home improvement"]])

        pool = await PoolBuilder(client, topics=["Home Improvement"]).build(
            cap=8, country="us", languages=("English",), preset_name="guest"
        )

        assert pool.keywords[:2] == ["home improvement write for us", "home improvement guest post"]
        assert pool.keywords[4] == "kitchen remodel write for us"

    async def test_a_niche_in_another_script_is_left_to_the_model(self) -> None:
        """Тема по-русски при английских шаблонах — две письменности в одном
        запросе: такую тему переводит модель, а не подставляем мы."""
        client = FakeClient([["sports betting", "betting tips"]])

        pool = await PoolBuilder(client, topics=["ставки на спорт"]).build(
            cap=8, country="us", languages=("English",), preset_name="guest"
        )

        assert pool.keywords[0] == "sports betting write for us"
        assert not any("ставки" in key for key in pool.keywords)

    async def test_near_duplicate_topics_are_dropped_not_queries(self) -> None:
        client = FakeClient([["home decor", "home decor ideas", "gardening"]])

        pool = await PoolBuilder(client).build(
            cap=8, country="us", languages=("English",), preset_name="guest"
        )

        assert not any(key.startswith("home decor ideas") for key in pool.keywords)
        assert "gardening write for us" in pool.keywords
        assert pool.report.near_duplicates == 1

    async def test_a_german_market_gets_german_footprints(self) -> None:
        client = FakeClient([["garten"]])

        pool = await PoolBuilder(client).build(
            cap=4, country="de", languages=("German",), preset_name="guest"
        )

        assert pool.keywords == [
            "garten gastbeitrag",
            "garten gastartikel",
            "garten gastautor werden",
            "garten gesponserter beitrag",
        ]

    async def test_a_bilingual_market_gets_both_vocabularies(self) -> None:
        client = FakeClient([["tuinieren", "fietsen"], ["jardinage", "cuisine"]])

        pool = await PoolBuilder(client).build(
            cap=10, country="be", languages=("Dutch", "French"), preset_name="guest"
        )

        assert "tuinieren schrijf voor ons" in pool.keywords
        assert "jardinage écrire pour nous" in pool.keywords
        assert len(pool.keywords) == 10

    async def test_a_market_without_footprints_refuses_before_spending(self) -> None:
        """Украинский и русский в таблице не заведены: отказ с выходом,
        а не английские слова в чужом рынке — и ни одного вызова модели."""
        client = FakeClient([["x"]])

        with pytest.raises(NoFootprintsError):
            await PoolBuilder(client).build(
                cap=10, country="ua", languages=("Ukrainian", "Russian"), preset_name="guest"
            )

        assert client.calls == 0
