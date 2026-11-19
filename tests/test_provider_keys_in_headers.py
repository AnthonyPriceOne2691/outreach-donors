"""Ключ платного провайдера — в заголовке, а не в адресе: боевые конструкторы (аудит 19.11.2026).

Тесты Ahrefs и DataForSEO отдают клиенту свой `httpx.AsyncClient`, и клиент, которого собирает
сам конструктор, — тот, что ходит в бою, — не исполнял ни один: правка, переложившая ключ в
параметр адреса, прошла бы зелёной. А адрес с ключом уходит в текст отказа провайдера, в журнал
и в историю запросов (docs/SECURITY.md). Здесь клиента собирает конструктор, а ответ даёт
подставленный транспорт httpx: запрос собран целиком, как для сети, но в сеть не уходит.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.serp.dataforseo import BALANCE_PATH, PROD_URL, DataForSeoProvider

#: Выдуманные значения: их не должно быть ни в одном адресе.
KEY = "ahrefs-key-for-tests"  # pragma: allowlist secret
LOGIN = "serp-login-for-tests"
PASSWORD = "serp-password-for-tests"  # pragma: allowlist secret

COST = {"x-api-units-cost-total-actual": "0", "x-api-units-cost-total": "0"}


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Транспорт по умолчанию у любого `httpx.AsyncClient` — без сети: запрос записывается,
    ответ — по пути запроса."""
    seen: list[httpx.Request] = []
    answers: dict[str, Any] = {
        "/v3/subscription-info/limits-and-usage": {"limits_and_usage": {"units_limit_api": 10}},
        "/v3/site-explorer/metrics-by-country": {"metrics": []},
        BALANCE_PATH: {
            "status_code": 20000,
            "tasks": [{"status_code": 20000, "result": [{"money": {"balance": 12.5}}]}],
        },
    }

    async def answered(_: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answers[request.url.path], headers=COST, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answered)
    return seen


def _clean_address(request: httpx.Request, *secrets: str) -> None:
    address = str(request.url)
    assert request.url.userinfo == b"", f"логин в адресе: {address}"
    for secret in secrets:
        assert secret not in address, f"секрет в адресе: {address}"


async def test_ahrefs_key_goes_in_the_bearer_header(wire: list[httpx.Request]) -> None:
    client = AhrefsClient(api_key=KEY)
    try:
        await client.limits_and_usage()
        await client.metrics_by_country("site.example.test", "2026-11-01")
    finally:
        await client.aclose()

    assert [request.url.path for request in wire] == [
        "/v3/subscription-info/limits-and-usage",
        "/v3/site-explorer/metrics-by-country",
    ]
    for request in wire:
        assert str(request.url).startswith(ahrefs_cfg.BASE_URL.rstrip("/"))
        assert request.headers["authorization"] == f"Bearer {KEY}"
        _clean_address(request, KEY)
    assert wire[1].url.params["target"] == "site.example.test", "параметры запроса — на месте"


async def test_dataforseo_login_goes_in_the_basic_header(wire: list[httpx.Request]) -> None:
    provider = DataForSeoProvider(sandbox=False, login=LOGIN, password=PASSWORD)
    try:
        balance = await provider.balance()
    finally:
        await provider.aclose()

    assert balance == 12.5
    [request] = wire
    assert str(request.url) == f"{PROD_URL}{BALANCE_PATH}", "боевой адрес провайдера"
    pair = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {pair}"
    _clean_address(request, LOGIN, PASSWORD, pair)
