"""Список ссылающихся доменов у Ahrefs: форма запроса, цена строки, расход и отказы.

Ни один тест не ходит в сеть и не тратит юниты: транспорт подменён (`httpx.MockTransport`),
ответы — в форме провода: список под ключом `refdomains`, цена — заголовками.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.client import MAX_ATTEMPTS, AhrefsClient, AhrefsError
from backend.features.ahrefs.units import MIN_REQUEST_UNITS, UnitsCost

#: Пороги по умолчанию: DR 20–80, трафик от 500.
FILTER = links.RefdomainsFilter(min_dr=20, max_dr=80, min_traffic=500)


def _cost(total: int, per_row: int, actual: int | None = None) -> dict[str, str]:
    return {
        "x-api-units-cost-total-actual": str(total if actual is None else actual),
        "x-api-units-cost-total": str(total),
        "x-api-units-cost-row": str(per_row),
    }


def _client(handler: Any, **kwargs: Any) -> AhrefsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http, **kwargs)


async def _no_sleep(_: float) -> None:
    return None


class TestRequestShape:
    async def test_thresholds_go_to_ahrefs_and_the_limit_is_always_sent(self) -> None:
        """Пороги — на стороне Ahrefs: строки вне них не приходят и не оплачиваются. Без
        `limit` ответ — весь список цели, и платим за каждую строку."""
        seen: dict[str, list[str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == links.PATH
            seen.update(parse_qs(urlparse(str(request.url)).query))
            return httpx.Response(200, json={"refdomains": []}, headers=_cost(50, 14))

        await _client(handler).refdomains("example.com", where=FILTER, limit=200)

        assert seen["target"] == ["example.com"]
        assert seen["mode"] == ["subdomains"]
        assert seen["protocol"] == ["both"]
        assert seen["history"] == ["live"]
        assert seen["select"] == ["domain,domain_rating"]
        assert seen["order_by"] == ["domain_rating:desc"]
        assert seen["limit"] == ["200"]
        assert json.loads(seen["where"][0]) == {
            "and": [
                {"field": "is_dofollow", "is": ["eq", True]},
                {"field": "is_spam", "is": ["eq", False]},
                {"field": "domain_rating", "is": ["gte", 20]},
                {"field": "domain_rating", "is": ["lte", 80]},
                {"field": "traffic_domain", "is": ["gte", 500]},
            ]
        }

    def test_no_traffic_threshold_means_no_traffic_filter(self) -> None:
        """Порог трафика ноль — узла нет: иначе платили бы десять юнитов за строку за
        фильтр, который ничего не режет."""
        loose = links.RefdomainsFilter(min_dr=10, max_dr=90, min_traffic=0)

        assert "traffic_domain" not in loose.fields
        assert loose.fields == {"is_dofollow", "is_spam", "domain_rating"}

    def test_dr_floor_above_the_big_site_line_is_refused(self) -> None:
        with pytest.raises(ValueError, match="выше границы крупного сайта"):
            links.RefdomainsFilter(min_dr=81, max_dr=80)

    @pytest.mark.parametrize("limit", [0, links.MAX_ROWS + 1])
    async def test_limit_outside_what_ahrefs_gives_is_refused_without_a_request(
        self, limit: int
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("запроса быть не должно")

        with pytest.raises(ValueError, match=f"от 1 до {links.MAX_ROWS}"):
            await _client(handler).refdomains("example.com", where=FILTER, limit=limit)

    async def test_unknown_target_mode_is_refused_without_a_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("запроса быть не должно")

        with pytest.raises(ValueError, match="Ahrefs не знает"):
            await _client(handler).refdomains("example.com", where=FILTER, limit=10, mode="site")


class TestRowPrice:
    """Модель цены строки обязана воспроизводить замеры живым ключом — иначе это догадка."""

    def test_our_linked_domains_measurement(self) -> None:
        """22.09.2026: четыре поля, одно ключевое, — 4 юнита строка (22 строки — 88)."""
        select = ("domain", "links_from_target", "dofollow_links", "linked_pages")

        assert links.row_units(select, ()) == 4
        assert links.list_units(22, links.row_units(select, ())) == 88

    def test_measurements_of_the_neighbouring_system_on_this_endpoint(self) -> None:
        """Семь полей в `select` и `is_dofollow` только в `where` — 8; с трафиком — 18."""
        select = (
            "domain",
            "domain_rating",
            "is_spam",
            "first_seen",
            "last_seen",
            "links_to_target",
            "dofollow_links",
        )
        where = {"is_dofollow", "is_spam", "domain_rating"}

        assert links.row_units(select, where) == 8
        assert links.row_units((*select, "traffic_domain"), where | {"traffic_domain"}) == 18

    def test_our_request_costs_fourteen_with_traffic_and_four_without(self) -> None:
        loose = links.RefdomainsFilter(min_dr=20, max_dr=80)

        assert links.row_units(links.SELECT, FILTER.fields) == 14
        assert links.row_units(links.SELECT, loose.fields) == 4

    def test_unknown_field_is_priced_above_any_measured_one(self) -> None:
        assert links.row_units(("domain", "never_measured"), ()) == 1 + links.UNKNOWN_FIELD_UNITS

    @pytest.mark.parametrize(
        ("rows", "per_row", "units"),
        [(0, 14, MIN_REQUEST_UNITS), (3, 14, MIN_REQUEST_UNITS), (200, 14, 2800), (1000, 4, 4000)],
    )
    def test_a_list_costs_its_rows_but_not_less_than_the_minimum(
        self, rows: int, per_row: int, units: int
    ) -> None:
        assert links.list_units(rows, per_row) == units


class TestUsageAccounting:
    async def test_cost_of_the_list_is_reported_as_its_own_operation(self) -> None:
        spent: list[tuple[str, UnitsCost]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            rows = [{"domain": "example.org", "domain_rating": 44.0}]
            return httpx.Response(200, json={"refdomains": rows}, headers=_cost(50, 14))

        response = await _client(handler, on_usage=lambda op, c: spent.append((op, c))).refdomains(
            "example.com", where=FILTER, limit=10
        )

        assert response.rows == [{"domain": "example.org", "domain_rating": 44.0}]
        assert [(op, c.billable, c.per_row) for op, c in spent] == [("refdomains", 50, 14)]

    async def test_list_served_from_ahrefs_cache_is_recorded_as_free(self) -> None:
        """Повторный запрос Ahrefs отдаёт из кэша за ноль — строка журнала всё равно есть."""
        spent: list[UnitsCost] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"refdomains": []}, headers=_cost(2800, 14, actual=0))

        await _client(handler, on_usage=lambda _op, cost: spent.append(cost)).refdomains(
            "example.com", where=FILTER, limit=200
        )

        assert spent[0].was_free
        assert spent[0].billable == 0

    async def test_refused_list_still_reports_what_it_cost(self) -> None:
        spent: list[UnitsCost] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden", headers=_cost(50, 14))

        with pytest.raises(AhrefsError):
            await _client(handler, on_usage=lambda _op, cost: spent.append(cost)).refdomains(
                "example.com", where=FILTER, limit=10
            )

        assert spent[0].billable == 50


class TestRefusals:
    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
    async def test_refusal_by_key_rights_or_request_is_permanent_and_names_its_code(
        self, status: int
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text="refused by provider")

        with pytest.raises(AhrefsError) as caught:
            await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert caught.value.permanent
        assert caught.value.status == status
        assert f"Ahrefs ответил {status}" in str(caught.value)

    @pytest.mark.parametrize("status", [429, 503])
    async def test_busy_provider_is_retried_and_then_is_worth_a_later_retry(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(status, text="slow down")

        with pytest.raises(AhrefsError) as caught:
            await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert calls["n"] == MAX_ATTEMPTS
        assert not caught.value.permanent
        assert caught.value.status == status
        assert "slow down" in str(caught.value)

    async def test_answer_in_an_unknown_shape_is_a_permanent_refusal_not_an_empty_list(
        self,
    ) -> None:
        """Пустой список — «ссылок нет», непонятная форма — «мы не поняли ответ»."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"refdomains": "не список"}, headers=_cost(50, 14))

        with pytest.raises(AhrefsError) as caught:
            await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert caught.value.permanent
        assert caught.value.status is None

    async def test_rows_are_taken_by_their_named_key_not_the_first_list(self) -> None:
        """Список ссылающихся доменов — под `refdomains`: первое списочное значение ответа
        могло бы оказаться не им, и сайты конкурента пропали бы как «ссылок нет»."""

        def handler(request: httpx.Request) -> httpx.Response:
            payload = {"warnings": [{"code": "note"}], "refdomains": [{"domain": "example.org"}]}
            return httpx.Response(200, json=payload, headers=_cost(50, 14))

        response = await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert response.rows == [{"domain": "example.org"}]

    async def test_answer_without_the_named_list_is_a_permanent_refusal(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = {"domains": [{"domain": "example.org"}]}
            return httpx.Response(200, json=payload, headers=_cost(50, 14))

        with pytest.raises(AhrefsError, match="нет списка «refdomains»") as caught:
            await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert caught.value.permanent

    async def test_network_failure_after_retries_has_no_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", _no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("сеть недоступна", request=request)

        with pytest.raises(AhrefsError) as caught:
            await _client(handler).refdomains("example.com", where=FILTER, limit=10)

        assert not caught.value.permanent
        assert caught.value.status is None
