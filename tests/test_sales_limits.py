"""Строки продаж на панели лимитов: настоящие маршруты панели, журнал расхода и снимок учётки.

Панель обещает человеку четыре вещи, и каждая проверяется на стыке, а не на функции: число в строке —
то, которым отказывает работа (`verify_cap`, журнал расхода); просчёт ничего не пишет и говорит, что
будет с месяцем; смена идёт за просчётом тем же сеттером и журналом, что у карточки, и новое значение
берёт следующий проход очистки; поиск адреса не смешивает этапы. Числа — выдуманные: тариф настоящей
учётки в публичный репозиторий не идёт.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.api.settings import routes as settings_routes
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.features.contacts.hunter_account import remember
from backend.features.contacts.search import CONTACTS_OPERATION
from backend.features.core import hunter_cap, usage
from backend.features.core.domain import AuditAction, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import HunterCapModel
from backend.features.ops.limit_fields import SAME
from backend.features.sales import limits, verify_cap
from backend.features.sales.segment_serp import SERP_OPERATION
from backend.features.sales.verifier import OPERATION as VERIFY_OPERATION
from backend.features.sales.verifier import Verdict
from backend.features.serp.funds import MIN_BALANCE_USD, dollars
from backend.shared.numbers import spaced
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_hunter_account import TEST_KEY, _account

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

PANEL = "/api/usage/limits"
QUOTE = "/api/usage/limits/quote"
CHANGE = "/api/usage/limits/change"
VERIFY = {"key": "sales.hunter.verify", "field": "per_month"}
#: Проверок тарифа у подставной учётки (`tests/test_hunter_account._account`).
TARIFF = 2000
#: Середина месяца: «сегодня», «этот месяц» и «прошлый месяц» не сливаются ни в какой день запуска.
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


class _Frozen(datetime):
    """Часы строк панели — закреплены: месяц и сутки счёта не зависят от дня запуска."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return NOW.astimezone(tz)


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user("лимиты@team.example.test", role=UserRole.ADMIN)


@pytest.fixture
async def token(admin: UserModel, sign_in: SignIn) -> str:
    return await sign_in(admin.email)


@pytest.fixture(autouse=True)
def env_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")


async def _panel(client: AsyncClient, token: str) -> dict[str, dict[str, Any]]:
    answer = await client.get(PANEL, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    return {row["key"]: row for row in answer.json()["rows"]}


async def _quote(client: AsyncClient, token: str, value: int) -> Any:
    answer = await client.post(QUOTE, json={**VERIFY, "value": value}, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    return answer.json()


async def _change(client: AsyncClient, token: str, value: int, quoted: int | None) -> Any:
    body = {**VERIFY, "value": value, "quoted": quoted}
    return await client.post(CHANGE, json=body, headers=bearer(token))


async def _account_known(session: AsyncSession) -> None:
    await remember(session, _account(), at=datetime.now(UTC))
    await session.commit()


def _spent(session: AsyncSession, operation: str, units: int, at: datetime) -> None:
    entry = usage.record(session, operation=operation, units=units)
    entry.created_at = at


async def _written(session: AsyncSession) -> tuple[int, int]:
    """Строки истории потолка и журнала действий: просчёт и отказ их не прибавляют."""
    return (
        int(await session.scalar(select(func.count()).select_from(HunterCapModel)) or 0),
        int(await session.scalar(select(func.count()).select_from(AuditLogModel)) or 0),
    )


class TestVerifyRow:
    async def test_the_env_holds_until_the_field_is_saved(
        self, client: AsyncClient, token: str
    ) -> None:
        row = (await _panel(client, token))["sales.hunter.verify"]

        assert row["per_month"] == 1500
        assert row["source"] == f".env {hunter_cap.SETTING}"
        assert row["editable"] == ["per_month"]
        assert row["task"] == "Продажи"
        assert row["cost_usd"] is None
        assert "кредита общего счёта" in row["cost_note"]

    async def test_a_saved_cap_wins_and_zero_is_a_number(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await hunter_cap.set_verify_cap(session, 0, ceiling=None, author=admin)
        await session.commit()

        row = (await _panel(client, token))["sales.hunter.verify"]

        assert row["per_month"] == 0
        assert row["source"] == limits.FROM_SCREEN
        assert row["now"].startswith("Проверки адресов остановлены")

    @pytest.mark.parametrize("raw", ["", "пять тысяч"])
    async def test_no_number_is_empty_says_why_and_stays_editable(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Настройка пуста или негодна: это и лечится полем, поэтому строка остаётся открытой."""
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", raw)

        row = (await _panel(client, token))["sales.hunter.verify"]

        assert row["per_month"] is None
        assert ("не задан" if raw == "" else "пять тысяч") in row["now"]
        assert row["editable"] == ["per_month"]

    async def test_used_is_the_number_the_cleaning_refuses_by(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(limits, "datetime", _Frozen)
        _spent(session, VERIFY_OPERATION, 3, NOW - timedelta(hours=1))
        _spent(session, VERIFY_OPERATION, 4, NOW - timedelta(days=3))
        _spent(session, VERIFY_OPERATION, 50, NOW - timedelta(days=20))
        await session.commit()

        row = (await _panel(client, token))["sales.hunter.verify"]

        assert (row["used_day"], row["used_month"]) == (3, 7)
        assert row["used_month"] == (await verify_cap.month(session, now=NOW)).spent
        assert f"на этот месяц осталось {spaced(1493)}" in row["now"]

    async def test_a_chosen_cap_says_the_cleaning_stands(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "7")
        _spent(session, VERIFY_OPERATION, 7, datetime.now(UTC))
        await session.commit()

        row = (await _panel(client, token))["sales.hunter.verify"]

        assert row["now"].startswith("Потолок месяца выбран")

    async def test_the_account_is_the_snapshot_and_not_a_call(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        before = (await _panel(client, token))["sales.hunter.verify"]
        await _account_known(session)

        after = (await _panel(client, token))["sales.hunter.verify"]

        assert "снимка учётки ещё нет" in before["now"]
        assert f"на учётке осталось {spaced(1499)}" in after["now"]


class TestQuote:
    async def test_it_writes_nothing_and_says_what_stays(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _account_known(session)
        _spent(session, VERIFY_OPERATION, 100, datetime.now(UTC))
        await session.commit()
        before = await _written(session)

        quoted = await _quote(client, token, 1000)

        assert await _written(session) == before
        assert quoted["refusal"] is None
        assert quoted["accepted"] == 1000
        assert quoted["cost_usd"] is None
        assert f"{spaced(1000)} проверок в месяц" in quoted["words"]
        assert "выбрано 100, останется 900" in quoted["words"]
        assert "50 % проверок тарифа" in quoted["words"]
        assert limits.RUNNING_KEEPS_OLD in quoted["words"]
        assert "кредита общего счёта" in quoted["words"]

    async def test_a_cap_below_the_chosen_is_allowed_and_warned(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        _spent(session, VERIFY_OPERATION, 300, datetime.now(UTC))
        await session.commit()

        quoted = await _quote(client, token, 200)

        assert quoted["refusal"] is None
        assert quoted["accepted"] == 200
        assert "проверки встанут до конца месяца" in quoted["words"]

    async def test_zero_stops_the_paid_part_and_needs_no_share(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _account_known(session)

        quoted = await _quote(client, token, 0)

        assert quoted["refusal"] is None
        assert "проверки адресов остановлены" in quoted["words"]
        assert "% проверок тарифа" not in quoted["words"]

    async def test_above_the_tariff_is_refused_in_the_cards_words(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _account_known(session)

        refused = await _quote(client, token, TARIFF + 1)
        fits = await _quote(client, token, TARIFF)

        assert refused["refusal"] == hunter_cap.refusal(TARIFF + 1, TARIFF)
        assert refused["accepted"] == 0
        assert fits["refusal"] is None
        assert "100 % проверок тарифа" in fits["words"]

    async def test_without_a_snapshot_the_border_is_only_a_typo(
        self, client: AsyncClient, token: str
    ) -> None:
        quoted = await _quote(client, token, TARIFF + 1)
        refused = await _quote(client, token, hunter_cap.MOST + 1)

        assert quoted["refusal"] is None
        assert "Снимка учётки ещё нет" in quoted["words"]
        assert refused["refusal"] == hunter_cap.refusal(hunter_cap.MOST + 1, None)

    async def test_below_zero_is_refused(self, client: AsyncClient, token: str) -> None:
        assert (await _quote(client, token, -1))["refusal"] == hunter_cap.refusal(-1, None)

    async def test_what_stands_now_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        from_env = await _quote(client, token, 1500)
        await hunter_cap.set_verify_cap(session, 900, ceiling=None, author=admin)
        await session.commit()
        from_field = await _quote(client, token, 900)

        assert from_env["refusal"] == SAME
        assert from_field["refusal"] == SAME

    @pytest.mark.parametrize(
        ("key", "field"),
        [
            ("sales.hunter.search", "per_month"),
            ("sales.serp.buy", "per_session"),
            ("sales.agent.turns", "per_session"),
        ],
    )
    async def test_a_read_only_row_refuses_with_its_own_words(
        self, client: AsyncClient, token: str, key: str, field: str
    ) -> None:
        answer = await client.post(
            QUOTE, json={"key": key, "field": field, "value": 5}, headers=bearer(token)
        )

        assert answer.status_code == 422
        assert (await _panel(client, token))[key]["now"] == answer.json()["detail"]


class TestChange:
    async def test_it_changes_what_the_card_changes(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Панель и карточка «Лимиты» — одно число: сторож, очистка и экран видят новое значение."""

        async def no_balance(*_: object) -> tuple[None, str]:
            return None, "нет"

        monkeypatch.setattr(settings_routes, "_ahrefs_balance", no_balance)
        monkeypatch.setattr(settings_routes, "_serp_balance", no_balance)
        await _account_known(session)
        quoted = await _quote(client, token, 1800)

        answer = await _change(client, token, 1800, quoted["accepted"])

        assert answer.status_code == 200, answer.text
        assert answer.json()["accepted"] == 1800
        assert await hunter_cap.stored_verify_cap(session) == 1800
        assert await hunter_cap.verify_month_cap(session) == 1800
        row = (await _panel(client, token))["sales.hunter.verify"]
        assert (row["per_month"], row["source"]) == (1800, limits.FROM_SCREEN)
        card = (await client.get("/api/usage", headers=bearer(token))).json()["hunter"]
        assert card["month"]["cap"] == 1800
        assert card["month"]["changes"][0]["set_by"] == admin.email

    async def test_the_journal_is_the_cards_journal(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        quoted = await _quote(client, token, 700)

        await _change(client, token, 700, quoted["accepted"])

        last = await session.scalar(select(HunterCapModel).order_by(HunterCapModel.id.desc()))
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.HUNTER_CAP_CHANGED)
        )
        assert last is not None
        assert entry is not None
        assert last.set_by == admin.id
        assert entry.user_id == admin.id
        assert entry.target == f"hunter_cap:{last.id}"
        assert entry.details == {
            "потолок проверок адреса": {
                "было": hunter_cap.cap_words(None),
                "стало": hunter_cap.cap_words(700),
            },
            "этап": Stage.SALES.value,
        }

    async def test_the_next_pass_takes_the_new_cap(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Шапка модуля: новое значение действует со следующего прохода очистки, а не на словах."""

        class Live:
            name = "hunter"

            async def verify(self, _email: str) -> Verdict:
                raise AssertionError("в тесте проверка не идёт")

        _spent(session, VERIFY_OPERATION, 5, datetime.now(UTC))
        await session.commit()
        running = await verify_cap.capped(session, Live())
        quoted = await _quote(client, token, 900)

        await _change(client, token, 900, quoted["accepted"])

        assert isinstance(running, verify_cap.MonthCapped)
        assert running.month.cap == 1500
        following = await verify_cap.capped(session, Live())
        assert isinstance(following, verify_cap.MonthCapped)
        assert following.month.cap == 900

    async def test_without_a_quote_nothing_changes(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        before = await _written(session)

        answer = await _change(client, token, 900, None)

        assert answer.status_code == 422
        assert "Сначала просчитайте" in answer.json()["detail"]
        assert await _written(session) == before

    async def test_a_quote_of_another_number_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        before = await _written(session)

        answer = await _change(client, token, 900, 800)

        assert answer.status_code == 422
        assert "Просчёт устарел" in answer.json()["detail"]
        assert await _written(session) == before

    async def test_a_cap_the_tariff_outgrew_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Снимок учётки обновился, пока человек читал окно: смена считает заново."""
        quoted = await _quote(client, token, TARIFF + 1)
        await _account_known(session)
        before = await _written(session)

        answer = await _change(client, token, TARIFF + 1, quoted["accepted"])

        assert answer.status_code == 422
        assert answer.json()["detail"] == hunter_cap.refusal(TARIFF + 1, TARIFF)
        assert await _written(session) == before

    async def test_what_stands_now_writes_no_history(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        before = await _written(session)

        answer = await _change(client, token, 1500, 1500)

        assert answer.status_code == 422
        assert answer.json()["detail"] == SAME
        assert await _written(session) == before

    async def test_a_panel_without_the_sales_right_has_no_sales_row(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        admin = await make_user(
            "без-продаж@team.example.test", role=UserRole.ADMIN, permissions={"sales": False}
        )
        token = await sign_in(admin.email)

        rows = await _panel(client, token)
        refused = await client.post(QUOTE, json={**VERIFY, "value": 900}, headers=bearer(token))

        assert "sales.hunter.verify" not in rows
        assert refused.status_code == 403
        assert "«Продажи»" in refused.json()["detail"]


class TestReadOnlyRows:
    async def test_the_search_counts_only_sales(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Операция общая: у продаж — записи с их этапом, у доноров — без них, и вместе это всё."""
        monkeypatch.setattr(limits, "datetime", _Frozen)
        for units, stage, at in (
            (2, Stage.SALES, NOW - timedelta(hours=1)),
            (5, Stage.SALES, NOW - timedelta(days=3)),
            (40, Stage.SALES, NOW - timedelta(days=20)),
            (9, Stage.DONORS, NOW - timedelta(hours=1)),
        ):
            entry = usage.record(session, operation=CONTACTS_OPERATION, units=units, stage=stage)
            entry.created_at = at
        await session.commit()

        rows = await _panel(client, token)

        row = rows["sales.hunter.search"]
        assert (row["used_day"], row["used_month"]) == (2, 7)
        assert (row["per_month"], row["per_session"], row["cost_usd"]) == (None, None, None)
        assert row["editable"] == []
        assert "смете" in row["now"]

    @pytest.mark.parametrize(
        ("flag", "key", "said"),
        [
            (True, TEST_KEY, "включён"),
            (False, TEST_KEY, "выключен"),
            (True, "", "выключен"),
        ],
    )
    async def test_the_paid_search_is_said_by_the_estimates_rule(
        self,
        client: AsyncClient,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
        flag: bool,
        key: str,
        said: str,
    ) -> None:
        """Включённый флаг без ключа не платит: то же правило, что у сметы прогона."""
        monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", flag)
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", key)

        row = (await _panel(client, token))["sales.hunter.search"]

        assert f"Платный поиск сбора {said}" in row["now"]

    async def test_the_serp_is_money_of_sales_only(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(limits, "datetime", _Frozen)
        for operation, amount, at in (
            (SERP_OPERATION, "1.25", NOW - timedelta(days=3)),
            (SERP_OPERATION, "9.00", NOW - timedelta(days=20)),
            ("serp_search", "4.00", NOW - timedelta(days=3)),
        ):
            entry = usage.record(session, operation=operation, amount_usd=Decimal(amount))
            entry.created_at = at
        await session.commit()

        row = (await _panel(client, token))["sales.serp.buy"]

        assert Decimal(row["spent_month_usd"]) == Decimal("1.25")
        assert (row["per_session"], row["unit"]) == (serp_cfg.MAX_KEYWORDS_PER_RUN, "ключей")
        assert row["per_month"] is None
        assert dollars(MIN_BALANCE_USD) in row["now"]
        assert row["editable"] == []

    async def test_the_turns_are_the_setting_of_one_dialog(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", "3")
        monkeypatch.setattr(sales_cfg, "AGENT_ENABLED", True)

        row = (await _panel(client, token))["sales.agent.turns"]

        assert (row["per_session"], row["session_hours"], row["scope"]) == (3, None, "один диалог")
        assert "отвечает человек" in row["now"]
        assert row["editable"] == []

    async def test_a_turned_off_agent_is_said(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "AGENT_ENABLED", False)

        row = (await _panel(client, token))["sales.agent.turns"]

        assert row["now"].startswith("Агент продаж выключен")

    async def test_a_bad_turns_setting_is_empty_and_says_why(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "AGENT_MAX_TURNS", "много")

        row = (await _panel(client, token))["sales.agent.turns"]

        assert row["per_session"] is None
        assert "SALES_AGENT_MAX_TURNS=«много»" in row["now"]


async def test_the_sales_rows_open_the_panel(client: AsyncClient, token: str) -> None:
    """Строки продаж — первой группой: сначала свои четыре, затем модель и почта общим кодом."""
    rows = list(await _panel(client, token))

    assert rows[:4] == [
        "sales.hunter.verify",
        "sales.hunter.search",
        "sales.serp.buy",
        "sales.agent.turns",
    ]
    assert rows[4] == "sales.llm"
    assert {"sales.mail.first", "sales.mail.box", "sales.mail.followup"} <= set(rows)
