"""Частота платного сервиса адресов: короткий повтор, а не проход вхолостую.

Сверка с постановкой 12.10.2026: у Hunter не было ни повторов, ни `Retry-After` —
первый же 429 делал домен «недоспрошенным», а следующие домены прохода били в тот же
лимит. Теперь запрос идёт общей обёрткой повторов (`shared/net/retry.with_retries`),
как у остальных провайдеров, а не отпустивший лимит останавливает проход словами.
Закрытая учётка и квота на том же 429 не повторяются: маркер решает раньше кода.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.provider import (
    ATTEMPTS,
    HunterProvider,
    ProviderBlockedError,
    ProviderError,
    ProviderRateLimitError,
    transient,
)
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import ContactStatus
from backend.shared.net.retry import MAX_DELAY_SEC
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_contacts_no_answer import NOTHING, Paid, Site, _mx_is_fine

__all__ = ["_mx_is_fine"]  # почта у доменов есть — иначе спуск до платной ступени не дойдёт

KEY = "hunter-key-not-for-logs-0451"
RATE = {"errors": [{"id": "too_many_requests", "code": 429, "details": "slow down"}]}
FOUND = {"data": {"emails": [{"value": "editor@site.example.test", "confidence": 95}]}}


def _hunter(*replies: httpx.Response) -> tuple[list[httpx.Request], httpx.AsyncClient]:
    """Hunter, отвечающий по очереди `replies`; последний ответ повторяется дальше."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[min(len(seen), len(replies)) - 1]

    return seen, httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Паузы между попытками — числами, без сна."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("backend.shared.net.retry._sleep", record)
    return slept


class TestTheProvider:
    async def test_a_rate_limit_that_passes_is_repeated(self, pauses: list[float]) -> None:
        seen, http = _hunter(httpx.Response(429, json=RATE), httpx.Response(200, json=FOUND))

        async with http:
            found = await HunterProvider(http, api_key=KEY).find_emails("site.example.test")

        assert [candidate.email for candidate in found] == ["editor@site.example.test"]
        assert len(seen) == 2
        assert len(pauses) == 1

    @pytest.mark.parametrize(("asked", "low"), [("3", 3.0), ("120", MAX_DELAY_SEC)])
    async def test_retry_after_is_obeyed_up_to_the_ceiling(
        self, pauses: list[float], asked: str, low: float
    ) -> None:
        """Пауза — сколько просил сервис, но не дольше общего потолка: «Retry-After: 120»
        посреди прохода — это две минуты на домен."""
        _, http = _hunter(
            httpx.Response(429, json=RATE, headers={"Retry-After": asked}),
            httpx.Response(200, json=FOUND),
        )

        async with http:
            await HunterProvider(http, api_key=KEY).find_emails("site.example.test")

        (pause,) = pauses
        assert low <= pause < low + 0.5

    async def test_a_limit_that_stays_is_a_rate_limit_after_the_repeats(
        self, pauses: list[float], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Повторы кончились — тот же исход, что раньше, словами; ключ — ни в отказе,
        ни в журнале повторов."""
        seen, http = _hunter(httpx.Response(429, json=RATE))
        caplog.set_level(logging.DEBUG)

        async with http:
            with pytest.raises(ProviderRateLimitError, match="частота превышена") as refused:
                await HunterProvider(http, api_key=KEY).find_emails("site.example.test")

        assert len(seen) == ATTEMPTS
        assert len(pauses) == ATTEMPTS - 1
        assert KEY not in str(refused.value)
        assert KEY not in caplog.text
        assert "платный сервис адресов: провайдер ответил 429" in caplog.text

    @pytest.mark.parametrize("marker", ["restricted_account", "usage_exceeded", "some_new"])
    async def test_a_refusal_named_by_its_marker_is_not_repeated(
        self, pauses: list[float], marker: str
    ) -> None:
        """Закрытая учётка и квота приезжают тем же 429 — повтор их не лечит, и лишнего
        вызова нет; незнакомый маркер за частоту тоже не выдаётся."""
        seen, http = _hunter(
            httpx.Response(429, json={"errors": [{"id": marker, "code": 429, "details": "x"}]})
        )

        async with http:
            with pytest.raises(ProviderError) as refused:
                await HunterProvider(http, api_key=KEY).find_emails("site.example.test")

        assert len(seen) == 1
        assert pauses == []
        assert not isinstance(refused.value, ProviderRateLimitError)
        if marker == "restricted_account":
            assert isinstance(refused.value, ProviderBlockedError)


class Limited(Paid):
    """Платный сервис, который ограничил частоту и после повторов не отпустил."""

    async def find_emails(self, host: str) -> list[Any]:
        self.calls.append(host)
        raise ProviderRateLimitError("частота превышена: slow down")


class TestThePass:
    async def test_the_ladder_stops_paying_after_the_limit(self) -> None:
        """Лимит не снялся на одном домене — следующий упёрся бы в тот же: платная ступень
        больше не зовётся, домен «недоспрошен», а не «адреса нет»."""
        limited = Limited()

        async with httpx.AsyncClient(transport=httpx.MockTransport(Site({"/": NOTHING}))) as http:
            ladder = ContactLadder(http, provider=limited)
            first = await ladder.find("one.example.test")
            second = await ladder.find("two.example.test")

        assert (first.status, second.status) == (ContactStatus.RATE_LIMITED,) * 2
        assert limited.calls == ["one.example.test"]
        assert ladder.halt.startswith("платный сервис ограничил частоту и после повторов")

    async def test_the_search_stops_paying_and_walks_the_rest_free(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limited = Limited()

        async def paid_step(_http: httpx.AsyncClient, _report: object) -> Limited:
            return limited

        monkeypatch.setattr("backend.features.contacts.search.paid_step", paid_step)
        monkeypatch.setattr("backend.config.contacts.PAID_SEARCH_ENABLED", True)
        monkeypatch.setattr("backend.features.contacts.search.BATCH", 1)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(Site({"/": NOTHING}))),
        )
        for host in ("one.example.test", "two.example.test", "three.example.test"):
            await make_donor(session, host)

        report = await search_contacts(session, limit=10)

        # Платный сервис спрошен один раз, а бесплатные ступени прошли все три домена:
        # обрыв прохода оставил бы остальные и без них (ревью продаж #358).
        assert (report.pending, report.walked, len(limited.calls)) == (3, 3, 1)
        said = (
            "Платная ступень остановлена: платный сервис ограничил частоту и после повторов "
            "не отпустил (частота превышена: slow down). Домены после неё прошли бесплатные "
            "ступени, платная — следующим проходом"
        )
        assert report.notes.count(said) == 1


@pytest.mark.parametrize(
    ("status", "marker", "repeat"),
    [
        (429, "too_many_requests", True),
        (429, "restricted_account", False),
        (429, "usage_exceeded", False),
        (429, "some_new_marker", False),
        (503, "some_new_marker", True),
        (503, "", True),
        (500, "usage_exceeded", False),
        (400, "wrong_params", False),
        (400, "blacklisted_domain", False),
        (503, "blacklisted_domain", False),
    ],
)
def test_what_is_repeated(status: int, marker: str, repeat: bool) -> None:
    """Повтор — частоте и перегрузке; закрытой учётке, квоте и незнакомому маркеру на 429 —
    нет (так пряталась закрытая учётка); 5xx с иным маркером — перегрузка (ревью продаж #358)."""
    body: dict[str, Any] = {"errors": [{"id": marker, "details": "x"}]} if marker else {}
    assert transient(httpx.Response(status, json=body)) is repeat
