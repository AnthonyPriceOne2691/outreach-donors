"""Кэш подсказки в расходе вызова модели (24.11.2026).

Провайдер называет в `usage.prompt_tokens_details`, сколько ввода прочитано из кэша
(`cached_tokens`) и сколько записано в него (`cache_write_tokens`, только шестое семейство).
Оба числа — части ввода: без них весь ввод шёл по цене ввода, и вызов с повторяющейся
подсказкой выглядел дороже, чем стоит. Тела ответов — как у gpt-6-luna на пробе 24.11.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.shared.llm import tokens_of
from backend.shared.llm_cache import CacheTokens, cache_tokens_of


def _answer(**details: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {"prompt_tokens": 1588, "completion_tokens": 120, "total_tokens": 1708}
    if details:
        usage["prompt_tokens_details"] = details
    return {"choices": [{"message": {"content": "{}"}}], "usage": usage}


def test_first_call_writes_the_prompt_to_the_cache() -> None:
    found = cache_tokens_of(_answer(cached_tokens=0, cache_write_tokens=1585))
    assert found == CacheTokens(read=0, written=1585)
    assert found.uncached(1588) == 3


def test_repeated_call_reads_the_prompt_from_the_cache() -> None:
    found = cache_tokens_of(_answer(cached_tokens=1552, cache_write_tokens=33))
    assert found == CacheTokens(read=1552, written=33)
    assert found.uncached(1588) == 3


def test_older_models_name_only_the_read() -> None:
    """У прежних моделей записи в кэш нет — ноль, а не отказ."""
    assert cache_tokens_of(_answer(cached_tokens=1024)) == CacheTokens(read=1024, written=0)


@pytest.mark.parametrize(
    "body",
    [
        _answer(),
        {"choices": []},
        {"usage": None},
        {"usage": {"prompt_tokens": 10, "prompt_tokens_details": None}},
        {"usage": {"prompt_tokens": 10, "prompt_tokens_details": ["cached_tokens", 5]}},
    ],
    ids=["no-details", "no-usage", "usage-null", "details-null", "details-not-a-dict"],
)
def test_without_details_nothing_is_cached(body: dict[str, Any]) -> None:
    """Нечего прочитать — весь ввод по цене ввода, как до учёта кэша."""
    found = cache_tokens_of(body)
    assert found == CacheTokens()
    assert found.uncached(1588) == 1588


@pytest.mark.parametrize(
    "details",
    [
        {"cached_tokens": "много", "cache_write_tokens": 33},
        {"cached_tokens": True, "cache_write_tokens": 33},
        {"cached_tokens": -5, "cache_write_tokens": 33},
        {"cached_tokens": 1552.0, "cache_write_tokens": 33},
    ],
    ids=["string", "bool", "negative", "float"],
)
def test_a_count_that_is_not_a_number_is_not_named(details: dict[str, Any]) -> None:
    """Не целое число — как не названо, по правилу `tokens_of`: ответ модели не роняется."""
    assert cache_tokens_of(_answer(**details)) == CacheTokens(read=0, written=33)


def test_more_cache_than_input_is_not_a_negative_input() -> None:
    """Провайдер ошибся в счёте — ввод по полной цене ноль, а не минус: счёт не уменьшается."""
    assert CacheTokens(read=1552, written=100).uncached(1588) == 0


def test_tokens_and_caps_are_counted_as_before() -> None:
    """Кэш меняет цену, а не число токенов: потолки в токенах считают весь ввод."""
    assert tokens_of(_answer(cached_tokens=1552, cache_write_tokens=33)) == 1708
