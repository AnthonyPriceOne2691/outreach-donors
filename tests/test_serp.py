"""Адаптер выдачи: что берём из ответа и чего не берём."""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.serp.ahrefs_serp import AhrefsSerpProvider
from backend.features.serp.protocol import SerpProvider

COST = {"x-api-units-cost-total-actual": "74", "x-api-units-cost-row": "2"}


def _provider(rows: list[dict], seen: dict | None = None) -> AhrefsSerpProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.update(parse_qs(urlparse(str(request.url)).query))
        return httpx.Response(200, json={"positions": rows}, headers=COST)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsSerpProvider(AhrefsClient(api_key="k", http=http))


def test_adapter_satisfies_the_protocol() -> None:
    """Второй провайдер встанет на это место без правок вызывающего кода."""
    assert isinstance(_provider([]), SerpProvider)


class TestRequest:
    async def test_metrics_are_not_requested(self) -> None:
        """С метриками тот же запрос стоит 481 юнит вместо 74, а DR и трафик
        дешевле взять пакетом — по 2 и 18 на домен."""
        seen: dict[str, list[str]] = {}
        await _provider([], seen).search(["best crm"], "us")

        assert seen["select"] == ["position,url"]
        assert "domain_rating" not in seen["select"][0]
        assert "traffic" not in seen["select"][0]

    async def test_country_goes_lowercase(self) -> None:
        seen: dict[str, list[str]] = {}
        await _provider([], seen).search(["best crm"], "US")

        assert seen["country"] == ["us"]


class TestParsing:
    ROWS: ClassVar[list[dict]] = [
        {"position": 1, "url": None},  # блок выдачи, не органический результат
        {"position": 1, "url": "https://a.com/x"},
        {"position": 2, "url": "https://b.com/y"},
        {"position": 3, "url": "https://c.com/z"},
    ]

    async def test_rows_without_url_are_skipped(self) -> None:
        """Ahrefs отдаёт и строки без адреса — «люди также спрашивают»
        и прочие блоки. Это не доноры."""
        results = (await _provider(self.ROWS).search(["k"], "us"))["k"]

        assert [r.url for r in results] == ["https://a.com/x", "https://b.com/y", "https://c.com/z"]

    async def test_depth_is_counted_in_pages_of_ten(self) -> None:
        """Единица тарификации у провайдеров — страница из десяти результатов,
        а не позиция. По  нам нужна одна страница."""
        results = (await _provider(self.ROWS).search(["k"], "us", depth_pages=1))["k"]
        assert len(results) == 3

    async def test_zero_depth_is_a_mistake_not_an_empty_result(self) -> None:
        """Запрос без глубины бессмысленен и почти наверняка ошибка вызова.
        Тихо вернуть пусто — значит отдать прогон без доноров и не сказать почему."""
        with pytest.raises(ValueError, match="минимум одна"):
            await _provider(self.ROWS).search(["k"], "us", depth_pages=0)

    async def test_every_keyword_is_present_in_the_answer(self) -> None:
        """Ключ без результатов возвращается с пустым списком, а не пропадает:
        иначе «ничего не нашлось» неотличимо от «ключ потерялся по дороге»."""
        answer = await _provider([]).search(["a", "b"], "us")
        assert set(answer) == {"a", "b"}
        assert answer["a"] == []

    async def test_results_are_capped_by_depth(self) -> None:
        rows = [{"position": i, "url": f"https://d{i}.com"} for i in range(1, 26)]
        results = (await _provider(rows).search(["k"], "us", depth_pages=1))["k"]
        assert len(results) == 10
