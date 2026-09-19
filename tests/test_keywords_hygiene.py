"""Гигиена фразы и дедупликация.

Обе темы про деньги. Плохая фраза — это оплаченный запрос выдачи,
который ничего не найдёт. Почти-дубль — это два оплаченных запроса
и один и тот же набор доменов: на нашем замере 83% выдачи схлопнулось
в повторы доменов.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.keywords.dedup import (
    drop_near_duplicates,
    interleave,
    split_over_angles,
)
from backend.features.keywords.hygiene import clean, normalize, parse_phrases, rejection_reason


class TestNormalize:
    def test_case_and_spaces(self) -> None:
        assert normalize("  Балита   Ngayon  ") == "балита ngayon"

    def test_dash_and_apostrophe_variants_unified(self) -> None:
        """Для поиска это разные символы, для человека один и тот же."""
        assert normalize("новости—дня") == normalize("новости-дня")
        assert normalize("l’actualité") == normalize("l'actualité")

    def test_diacritics_kept(self) -> None:
        """`piñol` и `mañana` — законные слова рынка, а не мусор."""
        assert normalize("elecciones mañana") == "elecciones mañana"


class TestRejection:
    @pytest.mark.parametrize(
        ("phrase", "marker"),
        [
            ("site:example.com новости", "оператор"),
            ("новости про team name", "подсказка"),
            ("новости PLACEHOLDER дня", "метка"),
            ('новости ["дня"]', "разметки"),
            ("одно два три четыре пять шесть семь восемь девять", "слов"),
        ],
    )
    def test_named_reasons(self, phrase: str, marker: str) -> None:
        reason = rejection_reason(phrase)
        assert reason is not None
        assert marker in reason

    def test_past_year_refused(self) -> None:
        past = datetime.now(UTC).year - 2
        reason = rejection_reason(f"итоги {past}")
        assert reason is not None
        assert "год" in reason

    def test_future_year_allowed(self) -> None:
        """«Выборы 2028» — живой запрос, а не протухший."""
        future = datetime.now(UTC).year + 2
        assert rejection_reason(f"выборы {future}") is None

    def test_mixed_scripts_refused(self) -> None:
        """Арабский корень внутри непальской фразы — модель смешала языки."""
        reason = rejection_reason("समाचार اليوم")
        assert reason is not None
        assert "письменности" in reason

    def test_latin_next_to_native_script_allowed(self) -> None:
        """Бренды и числа пишут латиницей — это норма, а не смешение."""
        assert rejection_reason("новости iphone сегодня") is None

    def test_east_asian_scripts_are_one_family(self) -> None:
        """Японский законно мешает кану и иероглифы в одной фразе."""
        assert rejection_reason("東京 ニュース") is None

    @pytest.mark.parametrize(
        "phrase", ["balita ngayon pilipinas", "ordinansa ng barangay", "новости дня"]
    )
    def test_good_phrases_pass(self, phrase: str) -> None:
        assert rejection_reason(phrase) is None


class TestClean:
    def test_rejected_are_returned_with_reasons(self) -> None:
        """Отсеянные нужны: по ним видно, что выдаёт модель на этом рынке,
        и только так настраивается промпт."""
        good, rejected = clean(["хорошая фраза", "site:site.com плохая"])

        assert good == ["хорошая фраза"]
        assert "оператор" in next(iter(rejected.values()))

    def test_exact_duplicates_collapse(self) -> None:
        good, _ = clean(["Новости Дня", "новости дня", "  новости   дня "])
        assert good == ["новости дня"]


class TestParse:
    def test_plain_array(self) -> None:
        assert parse_phrases('["раз", "два"]') == ["раз", "два"]

    def test_wrapped_in_backticks(self) -> None:
        assert parse_phrases('```json\n["раз"]\n```') == ["раз"]

    def test_wrapped_in_object(self) -> None:
        assert parse_phrases('{"phrases": ["раз", "два"]}') == ["раз", "два"]

    def test_truncated_answer_is_salvaged(self) -> None:
        """Ответ оборвался на токенном лимите. Терять весь набор из-за
        последней незакрытой кавычки слишком дорого."""
        assert parse_phrases('["раз", "два", "тр') == ["раз", "два"]

    def test_garbage_gives_empty(self) -> None:
        assert parse_phrases("модель ответила текстом") == []


class TestDeduplication:
    def test_near_duplicate_dropped(self) -> None:
        """«новости манилы» и «последние новости манилы» — это два
        оплаченных запроса с одной и той же выдачей."""
        kept = drop_near_duplicates(["новости манилы", "последние новости манилы"])
        assert kept == ["новости манилы"]

    def test_neighbours_by_topic_kept(self) -> None:
        """А «утренние» и «вечерние новости» — разные запросы."""
        kept = drop_near_duplicates(["утренние новости", "вечерние новости"])
        assert len(kept) == 2

    def test_first_of_a_group_survives(self) -> None:
        """Первым идёт то, что пришло от более дорогого угла."""
        kept = drop_near_duplicates(["цена размещения статьи", "цена размещения"])
        assert kept == ["цена размещения статьи"]

    def test_order_is_preserved(self) -> None:
        phrases = ["первый запрос", "второй иной", "третий другой"]
        assert drop_near_duplicates(phrases) == phrases


class TestSplit:
    def test_even_split(self) -> None:
        assert split_over_angles(24, 4) == [6, 6, 6, 6]

    def test_remainder_goes_to_the_first_angles(self) -> None:
        """Первым углам — тем, что дают больше доноров."""
        assert split_over_angles(10, 4) == [3, 3, 2, 2]
        assert sum(split_over_angles(10, 4)) == 10

    def test_zero_cap(self) -> None:
        assert split_over_angles(0, 3) == [0, 0, 0]


class TestInterleave:
    def test_languages_take_turns(self) -> None:
        """Без чередования первый язык занимает весь потолок, и второго
        в пуле не остаётся вовсе."""
        result = interleave([["a1", "a2", "a3"], ["b1", "b2", "b3"]], cap=4)
        assert result == ["a1", "b1", "a2", "b2"]

    def test_duplicates_across_pools_collapse(self) -> None:
        assert interleave([["один"], ["Один"]], cap=5) == ["один"]

    def test_cap_respected(self) -> None:
        assert len(interleave([[f"ф{i}" for i in range(10)]], cap=3)) == 3
