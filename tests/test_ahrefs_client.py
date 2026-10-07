"""Транспорт к Ahrefs. Ни один тест не ходит в сеть и не тратит юниты."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from backend.features.ahrefs.client import (
    MAX_BACKOFF_SEC,
    AhrefsClient,
    AhrefsError,
    _retry_delay,
)
from backend.features.ahrefs.units import MAX_BATCH_TARGETS, UnitsCost

COST_HEADERS = {
    "x-api-units-cost-total-actual": "55",
    "x-api-units-cost-total": "55",
    "x-api-units-cost-row": "11",
}

#: Ответ 200 со страницей вместо данных — так отвечают посредник и заглушка
#: на время работ.
PAGE = "<!doctype html><html><body>Service temporarily unavailable</body></html>"


def _client(handler: Any, **kwargs: Any) -> AhrefsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http, **kwargs)


class TestCountryLimitInvariant:
    """Самый дорогой промах проекта: без limit запрос стоит 1650 вместо 55."""

    async def test_limit_and_order_are_always_sent(self) -> None:
        seen: dict[str, list[str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(parse_qs(urlparse(str(request.url)).query))
            return httpx.Response(200, json={"metrics": []}, headers=COST_HEADERS)

        await _client(handler).metrics_by_country("example.com", "2026-09-01")

        assert seen["limit"] == ["5"]
        assert seen["order_by"] == ["org_traffic:desc"]

    async def test_limit_is_not_a_caller_parameter_that_can_be_omitted(self) -> None:
        """Метод не принимает «не ставить лимит» ни в каком виде."""
        with pytest.raises(ValueError, match="1650"):
            await _client(lambda r: httpx.Response(200, json={})).metrics_by_country(
                "example.com", "2026-09-01", top_n=0
            )


class TestBatching:
    async def test_batch_over_provider_limit_is_refused(self) -> None:
        hosts = [f"d{i}.com" for i in range(MAX_BATCH_TARGETS + 1)]
        with pytest.raises(ValueError, match="помещается 100"):
            await _client(lambda r: httpx.Response(200, json={})).batch_metrics(hosts, ["url"])

    async def test_empty_batch_costs_nothing_and_touches_no_network(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("запроса быть не должно")

        result = await _client(handler).batch_metrics([], ["url"])
        assert result.rows == []


class TestUsageAccounting:
    async def test_cost_is_reported_for_successful_request(self) -> None:
        spent: list[tuple[str, UnitsCost]] = []
        handler = lambda r: httpx.Response(200, json={"metrics": []}, headers=COST_HEADERS)  # noqa: E731

        await _client(handler, on_usage=lambda op, c: spent.append((op, c))).metrics_by_country(
            "example.com", "2026-09-01"
        )

        assert spent[0][0] == "by_country"
        assert spent[0][1].billable == 55

    async def test_cost_is_reported_even_when_request_fails(self) -> None:
        """Запрос, упавший после списания, всё равно списал. Не записать его
        значит развести наш счётчик с реальным — а счётчик общий с сервисом
        соседней системой."""
        spent: list[tuple[str, UnitsCost]] = []
        handler = lambda r: httpx.Response(403, text="forbidden", headers=COST_HEADERS)  # noqa: E731

        with pytest.raises(AhrefsError):
            await _client(handler, on_usage=lambda op, c: spent.append((op, c))).metrics_by_country(
                "example.com", "2026-09-01"
            )

        assert spent[0][1].billable == 55


class TestRetries:
    async def test_retries_on_429_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", _no_sleep)
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "1"})
            return httpx.Response(200, json={"metrics": [{"country": "us"}]}, headers=COST_HEADERS)

        result = await _client(handler).metrics_by_country("example.com", "2026-09-01")
        assert attempts["n"] == 2
        assert result.rows == [{"country": "us"}]

    async def test_does_not_retry_on_bad_key(self) -> None:
        """401 повторять бессмысленно: ключ не станет валидным от повтора,
        а каждая попытка может стоить юнитов."""
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(401, text="unauthorized")

        with pytest.raises(AhrefsError, match="401"):
            await _client(handler).metrics_by_country("example.com", "2026-09-01")
        assert attempts["n"] == 1

    async def test_exhausted_retries_name_what_the_provider_said(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Не удалось за N попыток» было одинаковым для перегрузки
        и для кончившихся юнитов — а это разные вещи: первое пройдёт само,
        второе не пройдёт никогда.

        Разделить их по коду мы не беремся: 429 у Ahrefs значит и то
        и другое, вызвать второе можно только исчерпав квоту. Но сказать,
        что именно ответил провайдер, обязаны.
        """
        monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", _no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text='{"error":"units limit reached"}')

        with pytest.raises(AhrefsError, match="units limit reached"):
            await _client(handler).metrics_by_country("example.com", "2026-09-01")

    def test_retry_after_is_honoured_but_capped(self) -> None:
        """Провайдеру верим, но не безоговорочно: «подождите час» не должно
        останавливать прогон на час."""
        short = httpx.Response(429, headers={"retry-after": "3"})
        assert _retry_delay(1, short) == 3.0

        absurd = httpx.Response(429, headers={"retry-after": "3600"})
        assert _retry_delay(1, absurd) == MAX_BACKOFF_SEC

    def test_garbage_retry_after_falls_back_to_backoff(self) -> None:
        broken = httpx.Response(429, headers={"retry-after": "not-a-number"})
        assert _retry_delay(2, broken) == 4.0


async def _no_sleep(_seconds: float) -> None:
    return None


class TestFailuresAreVisible:
    """Правило проекта: код не должен падать или врать без видимой причины."""

    async def test_unparsable_shape_is_an_error_not_an_empty_result(self) -> None:
        """Если провайдер сменит форму ответа, пустой список превратил бы это
        в «все домены неизвестны», и причину искали бы в данных, а не в коде."""
        handler = lambda r: httpx.Response(200, json="внезапно строка", headers=COST_HEADERS)  # noqa: E731

        with pytest.raises(AhrefsError, match="изменил"):
            await _client(handler).metrics_by_country("example.com", "2026-09-01")

    async def test_page_instead_of_json_is_a_refusal_in_words(self) -> None:
        """`JSONDecodeError` уходил мимо всех `except AhrefsError` — прогон падал
        технической ошибкой разбора. Страница вместо данных — не смена формы
        ответа: повтор может помочь, и ошибка не постоянная."""
        handler = lambda r: httpx.Response(200, text=PAGE, headers=COST_HEADERS)  # noqa: E731

        with pytest.raises(AhrefsError, match="by_country: ответ Ahrefs не разобран") as caught:
            await _client(handler).metrics_by_country("example.com", "2026-09-01")

        assert not caught.value.permanent
        assert "Service temporarily unavailable" in str(caught.value)

    async def test_quota_answered_with_a_page_is_a_refusal_in_words(self) -> None:
        """Тот же ответ на вопрос об остатке — «остаток недоступен», а не
        `ValueError`: его не ловил ни сторож тишины, ни запуск прогона."""
        handler = lambda r: httpx.Response(200, text=PAGE)  # noqa: E731

        with pytest.raises(AhrefsError, match="Остаток квоты недоступен: ответ Ahrefs") as caught:
            await _client(handler).limits_and_usage()

        assert not caught.value.permanent

    async def test_genuinely_empty_answer_is_not_an_error(self) -> None:
        """А пустой список — законный ответ: провайдер просто ничего не знает."""
        handler = lambda r: httpx.Response(200, json={"metrics": []}, headers=COST_HEADERS)  # noqa: E731

        result = await _client(handler).metrics_by_country("example.com", "2026-09-01")
        assert result.rows == []

    async def test_missing_cost_headers_are_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Молча записать ноль значит занизить учёт и однажды удивиться счёту:
        ключ общий с соседней системой."""
        handler = lambda r: httpx.Response(200, json={"metrics": []})  # noqa: E731

        with caplog.at_level("WARNING"):
            await _client(handler).metrics_by_country("example.com", "2026-09-01")

        assert "не вернул заголовки расхода" in caplog.text


class TestFreeRequestsAreRecorded:
    async def test_cached_response_reaches_the_journal(self) -> None:
        """Журнал без бесплатных строк выглядит так, будто запросов не делали,
        и по нему нельзя увидеть, что кэш провайдера работает."""
        seen: list[tuple[str, UnitsCost]] = []
        headers = {"x-api-units-cost-total-actual": "0", "x-api-units-cost-total": "55"}
        handler = lambda r: httpx.Response(200, json={"metrics": []}, headers=headers)  # noqa: E731

        await _client(handler, on_usage=lambda op, c: seen.append((op, c))).metrics_by_country(
            "example.com", "2026-09-01"
        )

        assert len(seen) == 1
        assert seen[0][1].was_free

    async def test_request_without_headers_is_not_recorded_as_free(self) -> None:
        seen: list[tuple[str, UnitsCost]] = []
        handler = lambda r: httpx.Response(200, json={"metrics": []})  # noqa: E731

        await _client(handler, on_usage=lambda op, c: seen.append((op, c))).metrics_by_country(
            "example.com", "2026-09-01"
        )

        assert seen == []
