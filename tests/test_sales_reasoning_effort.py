"""Ступень рассуждения у всех вызовов модели продаж — через общий перевод `shared.llm.reasoning_effort`.

Переход до 11.12.2026: пятое семейство снимают, замены — шестое (`gpt-6.1-terra`, `gpt-6.1-sol`).
Ступени `minimal` у шестого нет: запрос с ней получает 400 на каждом вызове — ответ лида уходил бы
человеку, агент молчал бы. Пятому семейству тело запроса прежнее до байта.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.sales import reply_kind, segment_client, segment_helper
from backend.features.sales.agent import calling


def bodies(model: str) -> dict[str, dict[str, Any]]:
    """Тело запроса каждого вызова модели продаж с рассуждением."""
    return {
        "reply_kind": reply_kind.build_payload(model, user="Re: предложение"),
        "agent": calling.payload(model, prompt=reply_kind.PROMPT_PATH, user="ответ лида"),
        "segment_helper": segment_helper.build_payload(model, draft="черновик описания"),
        "segment_client": segment_client.build_payload(model, user="сайт"),
    }


@pytest.mark.parametrize("model", ["gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-luna"])
def test_the_sixth_family_never_gets_a_step_it_does_not_have(model: str) -> None:
    """`minimal` у шестого семейства — `low` (совет провайдера), `low` — как есть."""
    efforts = {name: body["reasoning_effort"] for name, body in bodies(model).items()}

    assert efforts == dict.fromkeys(efforts, "low")


def test_the_fifth_family_keeps_its_steps_byte_for_byte() -> None:
    efforts = {name: body["reasoning_effort"] for name, body in bodies("gpt-5-mini").items()}

    assert efforts == {
        "reply_kind": "minimal",
        "agent": "minimal",
        "segment_helper": "low",
        "segment_client": "low",
    }
