"""Общий счёт кредитов Hunter (`contacts/hunter_budget.py`) и бюджет платной ступени прохода.

Проверяется то, ради чего счёт (25.11.2026): «найти» ищет всем ждущим разом, а учётка одна на
всех — свободное считается без запаса, проверок адресов до конца месяца и долей идущих поисков;
проход занимает долю под замком и тратит только её; кончилась доля — остальные домены прохода
«кончилась квота», а не платный запрос сверх; Hunter не ответил — снимок сторожа со своим
временем, а не пустое место. Сеть и Redis подменены, база не нужна.
"""

from __future__ import annotations

import time
from contextlib import AsyncExitStack
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from backend.features.contacts import hunter_budget, mx, search
from backend.features.contacts.hunter_account import AccountSnapshot
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.provider import (
    AccountCounter,
    HunterAccount,
    PaidUnavailable,
    ProviderError,
    Quota,
)
from backend.features.contacts.provider_step import SPENT_REASON
from backend.features.contacts.quality import Candidate
from backend.features.core.domain import ContactStatus

pytestmark = pytest.mark.asyncio

EMPTY = "<html><body><p>Пекарня у дома</p></body></html>"
ASKED = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)


def _counter(remaining: str) -> AccountCounter:
    left = Decimal(remaining)
    return AccountCounter(used=Decimal(10_000) - left, available=Decimal(10_000), remaining=left)


def _account(credits: str | None = "8284", searches: str = "8284") -> HunterAccount:
    return HunterAccount(
        plan="Growth",
        reset_on=date(2026, 11, 25),
        credits=None if credits is None else _counter(credits),
        searches=_counter(searches),
        verifications=_counter("16568"),
    )


class FakeRedis:
    """Хэш долей и замок — в памяти; `locked` — замок держит кто-то другой."""

    def __init__(self, *, locked: bool = False) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.keys: dict[str, str] = {"outreach:hunter:holds:lock": "1"} if locked else {}

    def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): v.encode() for k, v in self.hashes.get(name, {}).items()}

    def hset(self, name: str, key: str, value: str) -> int:
        self.hashes.setdefault(name, {})[key] = value
        return 1

    def hdel(self, name: str, key: str) -> int:
        return int(self.hashes.get(name, {}).pop(key, None) is not None)

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and name in self.keys:
            return False
        self.keys[name] = value
        return True

    def delete(self, name: str) -> int:
        return int(self.keys.pop(name, None) is not None)


@pytest.fixture
def account(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hunter, потолок проверок и снимок сторожа подменены; запас — 2000."""
    state: dict[str, Any] = {
        "live": _account(),
        "cap": 5000,
        "spent": 1539,
        "snapshot": None,
    }

    class Provider:
        def __init__(self, _http: object) -> None:
            pass

        async def account(self) -> HunterAccount:
            if isinstance(state["live"], Exception):
                raise state["live"]
            return state["live"]

    async def cap(_session: object) -> int | str:
        return state["cap"]

    async def spent(_session: object) -> int:
        return state["spent"]

    async def snapshot(_session: object) -> AccountSnapshot | None:
        return state["snapshot"]

    monkeypatch.setattr(hunter_budget, "HunterProvider", Provider)
    monkeypatch.setattr(hunter_budget, "verify_month_cap", cap)
    monkeypatch.setattr(hunter_budget, "month_spent", spent)
    monkeypatch.setattr(hunter_budget.hunter_account, "snapshot", snapshot)
    monkeypatch.setattr(hunter_budget.cfg, "PAID_RESERVE", 2000)
    return state


class TestEstimate:
    async def test_needed_and_affordable_by_half_a_request_per_site(self) -> None:
        assert hunter_budget.needed(2319) == 1160
        assert hunter_budget.affordable(300) == 600
        assert hunter_budget.needed(0) == 0

    async def test_free_is_never_below_zero(self) -> None:
        counted = hunter_budget.Budget(100, ASKED, True, reserve=2000, checks_hold=0, held=0)

        assert counted.free == 0

    async def test_free_leaves_the_reserve_the_checks_and_the_running_searches(
        self, account: dict[str, Any]
    ) -> None:
        redis = FakeRedis()
        redis.hset(hunter_budget.HOLDS_KEY, "job-1:aa", f"100|{time.time() + 600}")

        counted = await hunter_budget.budget(object(), object(), redis=redis)  # type: ignore[arg-type]

        assert isinstance(counted, hunter_budget.Budget)
        assert counted.account_left == 8284
        assert counted.live is True
        assert counted.reserve == 2000
        assert counted.checks_hold == 1731  # (5000 − 1539) × 0,5, вверх
        assert counted.held == 100
        assert counted.free == 8284 - 2000 - 1731 - 100

    async def test_plan_without_shared_credits_counts_searches_and_holds_no_checks(
        self, account: dict[str, Any]
    ) -> None:
        account["live"] = _account(credits=None, searches="900")

        counted = await hunter_budget.budget(object(), object(), redis=FakeRedis())  # type: ignore[arg-type]

        assert isinstance(counted, hunter_budget.Budget)
        assert counted.account_left == 900
        assert counted.checks_hold == 0

    async def test_cap_in_words_holds_no_checks(self, account: dict[str, Any]) -> None:
        account["cap"] = "потолок проверки адресов на месяц не задан"

        counted = await hunter_budget.budget(object(), object(), redis=FakeRedis())  # type: ignore[arg-type]

        assert isinstance(counted, hunter_budget.Budget)
        assert counted.checks_hold == 0

    async def test_silent_hunter_falls_back_to_the_watchdog_snapshot(
        self, account: dict[str, Any]
    ) -> None:
        account["live"] = ProviderError("сеть: таймаут")
        account["snapshot"] = AccountSnapshot(account=_account(credits="5000"), asked_at=ASKED)

        counted = await hunter_budget.budget(object(), object(), redis=FakeRedis())  # type: ignore[arg-type]

        assert isinstance(counted, hunter_budget.Budget)
        assert counted.live is False
        assert counted.asked_at == ASKED
        assert counted.account_left == 5000

    async def test_nothing_known_says_why(self, account: dict[str, Any]) -> None:
        account["live"] = ProviderError("сеть: таймаут")

        counted = await hunter_budget.budget(object(), object(), redis=FakeRedis())  # type: ignore[arg-type]

        assert counted == hunter_budget.UNKNOWN

    async def test_expired_holds_are_dropped(self) -> None:
        redis = FakeRedis()
        redis.hset(hunter_budget.HOLDS_KEY, "живой", f"40|{time.time() + 600}")
        redis.hset(hunter_budget.HOLDS_KEY, "умерший", f"900|{time.time() - 1}")

        assert hunter_budget.held(redis) == 40  # type: ignore[arg-type]
        assert set(redis.hashes[hunter_budget.HOLDS_KEY]) == {"живой"}


class TestClaim:
    async def test_takes_what_the_pass_needs_and_holds_it(self, account: dict[str, Any]) -> None:
        redis = FakeRedis()

        taken, counted = await hunter_budget.claim(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            token="job-7:ab",
            sites=100,
            ttl_sec=600,
            redis=redis,  # type: ignore[arg-type]
        )

        assert taken == 50
        assert isinstance(counted, hunter_budget.Budget)
        assert redis.hashes[hunter_budget.HOLDS_KEY]["job-7:ab"].startswith("50|")
        assert "outreach:hunter:holds:lock" not in redis.keys  # замок снят

    async def test_takes_only_what_is_free(self, account: dict[str, Any]) -> None:
        account["live"] = _account(credits="3741")  # свободно: 3741 − 2000 − 1731 = 10

        taken, _ = await hunter_budget.claim(
            object(),
            object(),
            token="t",
            sites=100,
            ttl_sec=600,
            redis=FakeRedis(),  # type: ignore[arg-type]
        )

        assert taken == 10

    async def test_nothing_free_holds_nothing(self, account: dict[str, Any]) -> None:
        account["live"] = _account(credits="3000")
        redis = FakeRedis()

        taken, counted = await hunter_budget.claim(
            object(),
            object(),
            token="t",
            sites=100,
            ttl_sec=600,
            redis=redis,  # type: ignore[arg-type]
        )

        assert taken == 0
        assert isinstance(counted, hunter_budget.Budget)
        assert counted.free == 0
        assert redis.hashes == {}

    async def test_lock_held_elsewhere_is_a_refusal_not_a_double_claim(
        self, account: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hunter_budget, "LOCK_WAIT_SEC", 0.0)

        with pytest.raises(hunter_budget.BudgetBusyError):
            await hunter_budget.claim(
                object(),
                object(),
                token="t",
                sites=10,
                ttl_sec=600,
                redis=FakeRedis(locked=True),  # type: ignore[arg-type]
            )

    async def test_release_frees_the_hold(self) -> None:
        redis = FakeRedis()
        redis.hset(hunter_budget.HOLDS_KEY, "t", f"50|{time.time() + 600}")

        await hunter_budget.release("t", redis)  # type: ignore[arg-type]

        assert hunter_budget.held(redis) == 0  # type: ignore[arg-type]


class _Provider:
    """Платный сервис прохода: считает вопросы, адресов не знает."""

    name = "подделка"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.asked.append(host)
        return []


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            return httpx.Response(200, html=EMPTY, request=request)
        return httpx.Response(404, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestPaidStepBudget:
    async def test_pass_stops_asking_once_its_credits_are_spent(
        self, pages: httpx.AsyncClient
    ) -> None:
        provider = _Provider()
        async with pages:
            ladder = ContactLadder(pages, provider=provider)
            ladder.limit_paid(1)

            first = await ladder.find("first.example.test")
            second = await ladder.find("second.example.test")

        assert provider.asked == ["first.example.test"]
        assert first.status is ContactStatus.NOT_FOUND
        assert second.status is ContactStatus.NO_QUOTA
        assert ladder.paid_spent is True

    async def test_no_budget_asks_as_before(self, pages: httpx.AsyncClient) -> None:
        provider = _Provider()
        async with pages:
            ladder = ContactLadder(pages, provider=provider)

            await ladder.find("first.example.test")
            await ladder.find("second.example.test")

        assert len(provider.asked) == 2
        assert ladder.paid_spent is False


class TestPassClaims:
    """`search._claim`: проход занимает кредиты до старта и отдаёт с концом."""

    async def test_taken_credits_become_the_budget_and_are_released(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        released: list[str] = []
        counted = hunter_budget.Budget(9000, ASKED, True, reserve=2000, checks_hold=0, held=0)

        async def claim(*_a: object, **kw: Any) -> tuple[int, hunter_budget.Budget]:
            assert kw["sites"] == 40
            return 20, counted

        async def release(token: str, redis: object = None) -> None:
            released.append(token)

        monkeypatch.setattr(hunter_budget, "claim", claim)
        monkeypatch.setattr(hunter_budget, "release", release)
        provider = _Provider()
        report = search.SearchReport()

        async with AsyncExitStack() as stack:
            got, budget = await search._claim(object(), object(), stack, report, provider, 40)  # type: ignore[arg-type]
            assert released == []

        assert got is provider
        assert budget == 20
        assert len(released) == 1
        assert report.notes == ["Кредиты Hunter: проходу занято 20 из свободных 7000."]

    async def test_nothing_free_means_the_step_waits_with_its_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counted = hunter_budget.Budget(3000, ASKED, True, reserve=2000, checks_hold=900, held=100)

        async def claim(*_a: object, **_kw: Any) -> tuple[int, hunter_budget.Budget]:
            return 0, counted

        monkeypatch.setattr(hunter_budget, "claim", claim)
        report = search.SearchReport()

        async with AsyncExitStack() as stack:
            got, budget = await search._claim(object(), object(), stack, report, _Provider(), 40)  # type: ignore[arg-type]

        assert isinstance(got, PaidUnavailable)
        assert got.status is ContactStatus.NO_QUOTA
        assert budget is None
        assert "свободных кредитов Hunter нет: на учётке 3000" in report.notes[0]
        assert report.paid_missing is got

    async def test_lock_not_given_is_a_search_error_retried_later(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def claim(*_a: object, **_kw: Any) -> tuple[int, hunter_budget.Budget]:
            raise hunter_budget.BudgetBusyError("замок не дался")

        monkeypatch.setattr(hunter_budget, "claim", claim)
        report = search.SearchReport()

        async with AsyncExitStack() as stack:
            got, budget = await search._claim(object(), object(), stack, report, _Provider(), 40)  # type: ignore[arg-type]

        assert isinstance(got, PaidUnavailable)
        assert got.status is ContactStatus.ERROR
        assert budget is None

    async def test_spent_reason_is_named(self) -> None:
        assert "кредиты Hunter" in SPENT_REASON
