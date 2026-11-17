"""Тариф вызова модели: явный «обычный», если вызов не назвал свой (17.11.2026).

Без `service_tier` провайдер берёт тариф проекта по умолчанию, а у проекта ключа это Fast —
вдвое дороже обычного. Тариф, который назвал сам вызов (Flex судьи продаж), не трогается; пустая
настройка — тело запроса прежнее до байта.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.shared import llm


KEY = "test-key"  # pragma: allowlist secret


def _payload(**extra: Any) -> dict[str, Any]:
    return {"model": "gpt-6.1-terra", "messages": [{"role": "user", "content": "x"}], **extra}


async def _posted(payload: dict[str, Any]) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        await llm.post_chat(http, api_key=KEY, payload=payload, topic="тест тарифа")
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
async def test_call_without_tier_goes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "SERVICE_TIER", "default")
    assert (await _posted(_payload()))["service_tier"] == "default"


@pytest.mark.asyncio
async def test_tier_named_by_call_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "SERVICE_TIER", "default")
    assert (await _posted(_payload(service_tier="flex")))["service_tier"] == "flex"


@pytest.mark.asyncio
async def test_empty_setting_keeps_body_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "SERVICE_TIER", "")
    payload = _payload()
    assert await _posted(payload) == payload


def test_tiered_does_not_touch_callers_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "SERVICE_TIER", "default")
    payload = _payload()
    assert llm.tiered(payload) == {**payload, "service_tier": "default"}
    assert "service_tier" not in payload
