"""Предохранитель счёта выдачи: на счету DataForSEO меньше $5 или меньше сметы — «пополните
счёт», и прогон не начинается (слово владельца 16.10.2026).

Проверяется на всех путях к покупке выдачи: смета (экран называет причину и не даёт нажать),
нажатие «Запустить» (отказ до строки прогона), сама покупка (`ensure_funds`) и сторож —
тревога «пополните счёт» на «Обзоре» и в Telegram. Остаток неизвестен — не отказ: о неответе
провайдера говорит своя тревога.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.run import RunModel
from backend.features.ops import funds_watch, silence
from backend.features.runs.failures import is_permanent
from backend.features.serp import funds
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import UnknownProviderError
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import _Plenty, known_key
from tests.test_api_runs_donors import RUN_BODY, FakeQueue

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


#: Настоящая функция — до подмены: `conftest` ставит «остаток неизвестен» всем тестам.
REAL_PROVIDER_BALANCE = funds.provider_balance


class _Account:
    """Провайдер выдачи со счётом: отвечает заданным остатком или отказом."""

    def __init__(self, balance: float | Exception) -> None:
        self._balance = balance
        self.closed = False

    async def balance(self) -> float:
        if isinstance(self._balance, Exception):
            raise self._balance
        return self._balance

    async def aclose(self) -> None:
        self.closed = True


def _balance(monkeypatch: pytest.MonkeyPatch, value: float | None) -> None:
    async def answer(_ahrefs: object) -> float | None:
        return value

    monkeypatch.setattr("backend.features.serp.funds.provider_balance", answer)


class TestWords:
    def test_money_is_written_like_the_screen(self) -> None:
        assert funds.dollars(3.2) == "3,20 $"
        assert funds.dollars(1234.5) == "1 234,50 $"

    def test_below_the_floor_refuses_any_run(self) -> None:
        words = funds.refusal(3.2, 0.11)
        assert words is not None
        assert words.startswith("На счету DataForSEO 3,20 $ — меньше 5,00 $")
        assert "пополните счёт DataForSEO. До пополнения прогоны не запускаются." in words

    def test_search_dearer_than_the_balance_refuses(self) -> None:
        words = funds.refusal(6.0, 7.0)
        assert words == (
            "Выдача этого прогона по смете ≈ 7,00 $, а на счету DataForSEO 6,00 $: "
            "пополните счёт DataForSEO."
        )

    @pytest.mark.parametrize(("balance", "cost"), [(10.0, 0.11), (5.0, 5.0), (None, 100.0)])
    def test_enough_or_unknown_is_no_refusal(self, balance: float | None, cost: float) -> None:
        assert funds.refusal(balance, cost) is None


class TestBalance:
    async def test_provider_without_an_account_is_unknown(self) -> None:
        assert await funds.balance_of(object()) is None

    async def test_silent_provider_is_unknown_not_a_refusal(self) -> None:
        assert await funds.balance_of(_Account(SerpError("молчит"))) is None
        assert await funds.balance_of(_Account(OSError("сеть"))) is None

    async def test_provider_from_settings_is_asked_and_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account = _Account(12.5)
        monkeypatch.setattr("backend.features.serp.funds.build_provider", lambda _client: account)

        assert await REAL_PROVIDER_BALANCE(object()) == 12.5  # type: ignore[arg-type]
        assert account.closed

    async def test_provider_not_configured_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unknown(_client: object) -> None:
            raise UnknownProviderError("SERP_PROVIDER не задан")

        monkeypatch.setattr("backend.features.serp.funds.build_provider", unknown)
        assert await REAL_PROVIDER_BALANCE(object()) is None  # type: ignore[arg-type]


class _Counted(_Account):
    """Счёт, который считает вопросы о себе."""

    def __init__(self, balance: float | Exception) -> None:
        super().__init__(balance)
        self.asked = 0

    async def balance(self) -> float:
        self.asked += 1
        return await super().balance()


def _older(seconds: float) -> None:
    """Состарить названный остаток: как будто его назвали `seconds` назад."""
    funds.LAST.at -= seconds


class TestMemory:
    """Названный остаток помнится (19.10.2026): сметы подряд не спрашивают провайдера каждый
    раз, а его отказ не выключает проверку счёта."""

    async def test_a_fresh_balance_is_not_asked_again(self) -> None:
        account = _Counted(40.0)
        assert await funds.balance_of(account) == 40.0
        assert await funds.balance_of(account) == 40.0
        assert account.asked == 1

    async def test_after_a_minute_the_provider_is_asked_again(self) -> None:
        assert await funds.balance_of(_Account(40.0)) == 40.0
        _older(funds.FRESH_S + 1)
        account = _Counted(12.0)
        assert await funds.balance_of(account) == 12.0
        assert account.asked == 1

    async def test_a_refusal_falls_back_to_the_balance_named_lately(self) -> None:
        assert await funds.balance_of(_Account(40.0)) == 40.0
        _older(funds.FRESH_S + 1)
        assert await funds.balance_of(_Account(SerpError("40202 Rate limit"))) == 40.0

    async def test_too_old_a_balance_is_not_used(self) -> None:
        assert await funds.balance_of(_Account(40.0)) == 40.0
        _older(funds.STALE_OK_S + 1)
        assert await funds.balance_of(_Account(SerpError("молчит"))) is None

    async def test_a_refusal_is_not_remembered(self) -> None:
        assert await funds.balance_of(_Account(SerpError("молчит"))) is None
        assert await funds.balance_of(_Account(7.5)) == 7.5

    async def test_the_purchase_asks_the_provider_even_after_a_fresh_balance(self) -> None:
        """Память — для смет, а не для траты: последняя проверка перед покупкой спрашивает
        провайдера сейчас (кросс-ревью продаж 19.10.2026)."""
        assert await funds.balance_of(_Account(40.0)) == 40.0
        account = _Counted(4.0)

        with pytest.raises(funds.FundsTooLowError):
            await funds.ensure_funds(account, 2, 1, "live")
        assert account.asked == 1

    async def test_the_purchase_still_falls_back_when_the_provider_refuses(self) -> None:
        assert await funds.balance_of(_Account(40.0)) == 40.0

        await funds.ensure_funds(_Account(SerpError("40202 Rate limit")), 2, 1, "live")


class TestBeforeThePurchase:
    async def test_low_balance_stops_before_buying_and_is_not_retried(self) -> None:
        with pytest.raises(funds.FundsTooLowError) as caught:
            await funds.ensure_funds(_Account(4.99), 2, 1, "live")
        assert "пополните счёт DataForSEO" in str(caught.value)
        assert is_permanent(caught.value)

    async def test_enough_money_lets_the_purchase_go(self) -> None:
        await funds.ensure_funds(_Account(50.0), 2, 1, "live")


class TestRoutes:
    async def test_estimate_names_the_balance_and_the_refusal(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await known_key(session)
        monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _Plenty)
        _balance(monkeypatch, 3.2)

        response = await client.post(
            "/api/runs/estimate", json=RUN_BODY, headers=bearer(admin_token)
        )

        assert response.status_code == 200, response.text
        made = response.json()
        assert made["serp_balance_usd"] == 3.2
        assert made["serp_refusal"].startswith("На счету DataForSEO 3,20")

    async def test_start_is_refused_before_the_run_row(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await known_key(session)
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        queue = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: queue)
        _balance(monkeypatch, 1.0)

        response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert response.status_code == 409, response.text
        assert "пополните счёт DataForSEO" in response.json()["detail"]
        assert queue.calls == []
        assert await session.scalar(select(func.count(RunModel.id))) == 0


class TestWatchdog:
    def test_low_balance_raises_the_top_up_alarm(self) -> None:
        funds_watch.remember(3.2)
        found = funds_watch.alarm()
        assert found is not None
        assert (found.code, found.title) == ("serp-funds-low", "Пополните счёт DataForSEO")
        assert "3,20" in found.detail

    @pytest.mark.parametrize("balance", [5.0, 120.0])
    def test_enough_is_quiet(self, balance: float) -> None:
        funds_watch.remember(balance)
        assert funds_watch.alarm() is None

    def test_unknown_is_quiet(self) -> None:
        assert funds_watch.alarm() is None

    async def test_the_providers_probe_remembers_the_balance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(silence, "build_provider", lambda _client: _Account(2.5))

        assert await silence._serp_silent() is None
        assert funds_watch.SEEN.balance == 2.5
