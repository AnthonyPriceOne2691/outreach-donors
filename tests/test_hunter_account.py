"""Hunter на экране «Расход»: учётка и потолок проверок адреса (слово Anthony 02.11.2026).

Проверяется: ответ `/account` в форме провода разбирается строго, а непонятый — громкий отказ;
снимок дробный, один и не откатывается старым ответом; потолок — настройка, ноль в том числе,
иначе `SALES_VERIFIER_MONTH_CAP` числом или словами; опрос кладёт ответ в снимок после сети, а отказ
Hunter — строка журнала, снимок цел и «провайдер не отвечает» не горит; сигнал 80, 95 и 100 % по
старшему из двух упоров; экран видит учётку всем, потолок — с правом «Продажи»; менять — правом
«менять потолки расхода» и «Продажи», отказы словами; ревизия ходит вниз и вверх. Числа — выдуманные.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.settings import routes as settings_routes
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.contacts.hunter_account import AccountSnapshot, remember, snapshot
from backend.features.contacts.provider import (
    AccountCounter,
    HunterAccount,
    HunterProvider,
    ProviderError,
    ProviderKeyRefusedError,
    parse_account,
)
from backend.features.core import hunter_cap, usage
from backend.features.core.domain import AuditAction, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import HunterAccountSnapshotModel, HunterCapModel
from backend.features.ops import hunter_watch, key_watch, silence
from backend.features.ops.alarms import Alarm
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.ahrefs_key import snapshot as key_snapshot
from backend.features.runs.spending import units_this_month
from backend.features.sales.verifier import OPERATION
from backend.workers import reaper
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer
from tests.migration_helpers import load_migration
from tests.test_ahrefs_key import PAYLOAD, _Answering, _no_serp
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/usage/hunter-cap"
MIGRATION = "3b7e1c94d2a6_hunter_caps.py"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NO_SALES = frozenset({Stage.DONORS, Stage.ADVERTISERS})
TEST_KEY = "test-key"  # pragma: allowlist secret

#: Ответ `/v2/account` в форме провода: числа выдуманные, поля — как у живого ответа. Имя и почта
#: владельца в ответе есть, и в снимок, журнал и отказы они попасть не должны.
WIRE: dict[str, Any] = {
    "data": {
        "first_name": "Секрет",
        "last_name": "Владельца",
        "email": "owner@team.example.test",
        "plan_name": "Team plan",
        "plan_level": 2,
        "reset_date": "2026-11-19",
        "team_id": 7,
        "requests": {
            "credits": {"used": 250.5, "available": 1000.0, "remaining": 749.5},
            "searches": {"used": 250, "available": 1000, "remaining": 749},
            "verifications": {"used": 501, "available": 2000, "remaining": 1499},
        },
    },
    "meta": {"params": {}},
}


def _account(
    *,
    credits_left: str | None = "749.5",
    verifications_left: str = "1499",
    plan: str = "Team plan",
) -> HunterAccount:
    """Учётка с общим счётом на 1 000 кредитов и 2 000 проверок; `credits_left` пусто — счёта нет."""
    return HunterAccount(
        plan=plan,
        reset_on=date(2026, 11, 19),
        credits=(
            None
            if credits_left is None
            else AccountCounter(
                used=Decimal(1000) - Decimal(credits_left),
                available=Decimal(1000),
                remaining=Decimal(credits_left),
            )
        ),
        searches=AccountCounter(Decimal(250), Decimal(1000), Decimal(749)),
        verifications=AccountCounter(
            used=Decimal(2000) - Decimal(verifications_left),
            available=Decimal(2000),
            remaining=Decimal(verifications_left),
        ),
    )


def _wire(transport: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(transport))


class TestParse:
    """Ответ — в форме провода: тот же JSON, что отдаёт Hunter."""

    def test_the_whole_account_with_a_fractional_credit(self) -> None:
        account = parse_account(WIRE["data"])

        assert account.plan == "Team plan"
        assert account.reset_on == date(2026, 11, 19)
        assert account.credits == AccountCounter(Decimal("250.5"), Decimal(1000), Decimal("749.5"))
        assert account.verifications.remaining == 1499
        assert account.searches.available == 1000

    def test_no_credits_block_is_a_plan_without_a_common_pool(self) -> None:
        data = json.loads(json.dumps(WIRE["data"]))
        del data["requests"]["credits"]

        assert parse_account(data).credits is None

    @pytest.mark.parametrize(
        "broken",
        [
            {"credits": {"used": "250", "available": 1000, "remaining": 750}},
            {"credits": {"used": True, "available": 1000, "remaining": 750}},
            {"credits": {"used": math.nan, "available": 1000, "remaining": 750}},
            {"credits": {"used": 250, "available": 1000}},
            {"credits": "много"},
            {"verifications": None},
        ],
        ids=["строка", "да-нет", "не число", "нет остатка", "не блок", "счётчика нет"],
    )
    def test_a_counter_that_is_not_understood_is_a_loud_refusal(
        self, broken: dict[str, Any]
    ) -> None:
        data = json.loads(json.dumps(WIRE["data"]))
        data["requests"].update(broken)

        with pytest.raises(ProviderError) as refused:
            parse_account(data)

        assert "владельца" not in str(refused.value).lower()
        assert "owner@" not in str(refused.value)

    @pytest.mark.parametrize(
        ("field", "value", "said"),
        [
            ("plan_name", None, "имени тарифа"),
            ("plan_name", "  ", "имени тарифа"),
            ("reset_date", None, "даты сброса"),
            ("reset_date", "25.11.2026", "не разобрана"),
            ("requests", [], "блока счётчиков"),
        ],
    )
    def test_a_missing_field_is_named(self, field: str, value: object, said: str) -> None:
        with pytest.raises(ProviderError, match=said):
            parse_account({**WIRE["data"], field: value})

    async def test_the_key_goes_in_the_header_and_the_address_stays_clean(self) -> None:
        seen: list[httpx.Request] = []

        def hunter(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=WIRE)

        async with _wire(hunter) as http:
            account = await HunterProvider(http, api_key=TEST_KEY).account()

        assert account.plan == "Team plan"
        assert seen[0].url.path == "/v2/account"
        assert "api_key" not in str(seen[0].url)
        assert seen[0].headers["authorization"].startswith("Bearer ")

    async def test_a_refused_key_stays_a_refused_key(self) -> None:
        async with _wire(lambda _: httpx.Response(401, json={"errors": []})) as http:
            with pytest.raises(ProviderKeyRefusedError):
                await HunterProvider(http, api_key=TEST_KEY).account()


class TestCounter:
    @pytest.mark.parametrize(
        ("available", "remaining", "percent"),
        [
            ("1000", "749.5", 25),
            ("1000", "200.5", 79),
            ("1000", "200", 80),
            ("1000", "0", 100),
            ("1000", "-5", 100),
            ("0", "0", 100),
            ("1000", "1200", 0),
        ],
        ids=["дробный", "вниз", "ровно", "всё", "ниже нуля", "тарифа нет", "докупили"],
    )
    def test_the_share_rounds_down_and_stays_in_bounds(
        self, available: str, remaining: str, percent: int
    ) -> None:
        counter = AccountCounter(Decimal(0), Decimal(available), Decimal(remaining))

        assert counter.used_percent == percent


class TestSnapshot:
    async def test_the_fractional_credit_comes_back_as_it_was(self, session: AsyncSession) -> None:
        assert await snapshot(session) is None

        await remember(session, _account(), at=NOW)
        found = await snapshot(session)

        assert found is not None
        assert found.asked_at == NOW
        assert found.account == _account()
        assert found.main == _account().credits

    async def test_without_a_common_pool_verifications_are_the_main_counter(
        self, session: AsyncSession
    ) -> None:
        await remember(session, _account(credits_left=None), at=NOW)

        found = await snapshot(session)

        assert found is not None
        assert found.account.credits is None
        assert found.main == found.account.verifications

    async def test_one_row_and_the_new_answer_lies_on_it(self, session: AsyncSession) -> None:
        await remember(session, _account(), at=NOW)
        await remember(session, _account(credits_left="100"), at=NOW + timedelta(minutes=10))

        found = await snapshot(session)

        total = await session.scalar(select(func.count()).select_from(HunterAccountSnapshotModel))
        assert total == 1
        assert found is not None
        assert found.main.remaining == 100

    async def test_an_older_answer_does_not_bring_the_past_back(
        self, session: AsyncSession
    ) -> None:
        await remember(session, _account(credits_left="100"), at=NOW)
        await remember(session, _account(), at=NOW - timedelta(minutes=10))

        found = await snapshot(session)

        assert found is not None
        assert found.main.remaining == 100
        assert found.asked_at == NOW

    async def test_a_long_plan_name_does_not_break_the_write(self, session: AsyncSession) -> None:
        await remember(session, _account(plan="Т" * 200), at=NOW)

        found = await snapshot(session)

        assert found is not None
        assert len(found.account.plan) == 64

    async def test_no_personal_data_is_in_the_row(self, session: AsyncSession) -> None:
        """В снимок идут только разобранные поля: имя и почта владельца остаются в ответе."""
        await remember(session, parse_account(WIRE["data"]), at=NOW)

        row = (await session.execute(text(f"SELECT * FROM {SNAPSHOT_TABLE}"))).mappings().one()

        assert "owner@" not in str(dict(row))
        assert "Владельца" not in str(dict(row))


SNAPSHOT_TABLE = HunterAccountSnapshotModel.__tablename__


class TestCap:
    """Договор с продажами: имена, сигнатуры и слова `verify_month_cap` не меняются."""

    def test_the_operation_is_the_one_the_sales_module_writes(self) -> None:
        assert hunter_cap.VERIFY_OPERATION == OPERATION

    async def test_nothing_saved_nothing_stored(self, session: AsyncSession) -> None:
        assert await hunter_cap.stored_verify_cap(session) is None

    async def test_the_env_number_acts_until_saved(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")

        assert await hunter_cap.verify_month_cap(session) == 1500

    async def test_no_env_says_where_to_set_it(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "")

        assert await hunter_cap.verify_month_cap(session) == (
            "потолок проверки адресов на месяц не задан — задайте его на экране «Расход»"
        )

    async def test_a_typo_in_env_is_said_with_the_words_the_sales_module_used(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Текст — договор с продажами: их разбор переезжает сюда со своими словами."""
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "полторы тысячи")

        assert await hunter_cap.verify_month_cap(session) == (
            "SALES_VERIFIER_MONTH_CAP «полторы тысячи» — нужно целое число проверок адреса в "
            "месяц, не меньше нуля"
        )

    async def test_the_last_saved_row_wins_and_zero_is_a_value(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        make_user: MakeUser,
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")
        author = await make_user("потолок@team.example.test", role=UserRole.ADMIN)

        await hunter_cap.set_verify_cap(session, 5000, ceiling=None, author=author)
        assert await hunter_cap.verify_month_cap(session) == 5000

        await hunter_cap.set_verify_cap(session, 0, ceiling=None, author=author)
        assert await hunter_cap.stored_verify_cap(session) == 0
        assert await hunter_cap.verify_month_cap(session) == 0

    async def test_the_change_names_what_stood_before(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        author = await make_user("потолок@team.example.test", role=UserRole.ADMIN)

        first = await hunter_cap.set_verify_cap(session, 100, ceiling=None, author=author)
        second = await hunter_cap.set_verify_cap(session, 200, ceiling=None, author=author)

        assert first.was is None
        assert second.was == 100
        changes = await hunter_cap.cap_changes(session)
        assert [change.verify_cap for change in changes] == [200, 100]
        assert changes[0].set_by == author.email

    @pytest.mark.parametrize(
        ("wanted", "ceiling", "said"),
        [
            (-1, None, "не меньше нуля"),
            (2001, 2000, "выше проверок тарифа"),
            (hunter_cap.MOST + 1, None, "не больше"),
        ],
        ids=["меньше нуля", "выше тарифа", "опечатка без снимка"],
    )
    async def test_a_bad_cap_is_refused_before_the_write(
        self,
        session: AsyncSession,
        make_user: MakeUser,
        wanted: int,
        ceiling: int | None,
        said: str,
    ) -> None:
        author = await make_user("потолок@team.example.test", role=UserRole.ADMIN)

        with pytest.raises(hunter_cap.HunterCapError, match=said):
            await hunter_cap.set_verify_cap(session, wanted, ceiling=ceiling, author=author)

        assert await session.scalar(select(func.count()).select_from(HunterCapModel)) == 0

    def test_the_tariff_itself_is_allowed(self) -> None:
        assert hunter_cap.refusal(2000, 2000) is None
        assert hunter_cap.refusal(0, 2000) is None

    @pytest.mark.parametrize(
        ("value", "said"),
        [
            (None, "как в настройках сервера"),
            (0, "0 — проверки адресов остановлены"),
            (5000, "5 000 проверок в месяц"),
        ],
    )
    def test_the_journal_words(self, value: int | None, said: str) -> None:
        assert hunter_cap.cap_words(value) == said


class TestAsk:
    @pytest.fixture
    def key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", TEST_KEY)

    @staticmethod
    def _answers(monkeypatch: pytest.MonkeyPatch, status: int, body: object) -> list[str]:
        asked: list[str] = []

        def hunter(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.path)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(hunter_watch, "_http", lambda: _wire(hunter))
        return asked

    async def test_no_key_no_question_and_no_noise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
        asked = self._answers(monkeypatch, 200, WIRE)

        with caplog.at_level(logging.WARNING):
            await hunter_watch.ask()

        assert asked == []
        assert caplog.text == ""
        assert hunter_watch.HEARD.account is None

    @pytest.mark.usefixtures("key")
    async def test_heard_then_kept_once(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._answers(monkeypatch, 200, WIRE)

        await hunter_watch.ask()

        assert await hunter_watch.keep_heard(session)
        assert not await hunter_watch.keep_heard(session), "положенное второй раз не кладётся"
        found = await snapshot(session)
        assert found is not None
        assert found.main.remaining == Decimal("749.5")

    @pytest.mark.usefixtures("key")
    @pytest.mark.parametrize(
        ("status", "body", "said"),
        [
            (401, {"errors": []}, "ключ платного сервиса не принят"),
            (503, {"errors": [{"id": "x", "details": "занят"}]}, "учётка не ответила"),
            (200, {"data": {"plan_name": "Team plan"}}, "учётка не ответила"),
        ],
        ids=["ключ", "сбой", "непонятый ответ"],
    )
    async def test_a_refusal_is_a_journal_line_and_the_snapshot_stays(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        status: int,
        body: object,
        said: str,
    ) -> None:
        await remember(session, _account(), at=NOW)
        self._answers(monkeypatch, status, body)

        with caplog.at_level(logging.WARNING):
            await hunter_watch.ask()

        assert said in caplog.text
        assert TEST_KEY not in caplog.text
        assert not await hunter_watch.keep_heard(session)
        found = await snapshot(session)
        assert found is not None
        assert found.asked_at == NOW, "снимок остаётся со своим временем"

    async def test_a_key_that_cannot_be_a_header_does_not_stop_the_watch_and_is_not_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Заголовок — только ASCII: кириллица в ключе падала бы мимо отказов провайдера и роняла
        весь проход сторожа, а текст такого исключения содержит сам ключ."""
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "ключ-с-опечаткой")
        self._answers(monkeypatch, 200, WIRE)

        with caplog.at_level(logging.WARNING):
            await hunter_watch.ask()

        assert "ключ Hunter не годится для заголовка" in caplog.text
        assert "опечаткой" not in caplog.text
        assert hunter_watch.HEARD.account is None

    @pytest.mark.usefixtures("key")
    async def test_a_hanging_hunter_does_not_hold_the_probe(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def hanging(_: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            raise AssertionError("сюда не дойти")

        monkeypatch.setattr(hunter_watch, "ASK_BUDGET_SEC", 0.01)
        monkeypatch.setattr(
            hunter_watch, "_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hanging))
        )

        with caplog.at_level(logging.WARNING):
            await hunter_watch.ask()

        assert "учётка не ответила" in caplog.text
        assert hunter_watch.HEARD.account is None

    @pytest.mark.usefixtures("key")
    async def test_hunter_is_not_among_the_providers_that_stop_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отказ Hunter не идёт в «Провайдер не отвечает»: прогоны от него не зависят."""

        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)
        asked = self._answers(monkeypatch, 503, {"errors": []})

        assert await silence.ask_providers() == []
        assert asked, "опрос спрашивает и Hunter"
        assert silence.WATCH.silent == {}

    @pytest.mark.usefixtures("key")
    async def test_both_snapshots_are_kept_and_one_does_not_hide_the_other(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._answers(monkeypatch, 200, WIRE)
        await hunter_watch.ask()
        key_watch.heard(PAYLOAD)

        assert await silence.keep_heard(session)

        assert await snapshot(session) is not None
        assert await key_snapshot(session) is not None
        assert not await silence.keep_heard(session)

    @pytest.mark.usefixtures("key")
    async def test_the_watch_pass_keeps_it_after_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)
        monkeypatch.setattr(reaper, "PROVIDERS", reaper.Providers())
        self._answers(monkeypatch, 200, WIRE)

        async def tell(_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(reaper.FEED, "tell", tell)
        async with committed_sessions() as factory:
            await reaper.watch()
            async with factory() as reading:
                found = await snapshot(reading)

        assert found is not None
        assert found.main.remaining == Decimal("749.5")

    @pytest.mark.usefixtures("key")
    async def test_the_watchdog_screen_keeps_it_too(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(silence, "AhrefsClient", _Answering)
        monkeypatch.setattr(silence, "_serp_silent", _no_serp)
        self._answers(monkeypatch, 200, WIRE)
        await make_user("смотрит-hunter@team.example.test")
        headers = bearer(await sign_in("смотрит-hunter@team.example.test"))

        response = await client.get("/api/watchdog", headers=headers)

        assert response.status_code == 200, response.text
        assert await snapshot(session) is not None


class TestStanding:
    @staticmethod
    def _found(cap: int | str, spent: int, account: AccountSnapshot | None = None) -> Any:
        return hunter_watch.Standing(snapshot=account, cap=cap, spent=spent)

    def test_zero_is_not_chosen_it_is_stopped(self) -> None:
        found = self._found(0, 5)

        assert found.month_left == 0
        assert found.month_percent is None

    def test_words_instead_of_a_number(self) -> None:
        found = self._found("слова", 5)

        assert found.month_left is None
        assert found.month_percent is None

    def test_spent_over_the_cap_stays_in_bounds(self) -> None:
        found = self._found(100, 150)

        assert (found.month_left, found.month_percent) == (0, 100)

    @pytest.mark.parametrize(
        ("value", "said"),
        [
            (Decimal("1234.5"), "1 234,5"),
            (Decimal("10000.00"), "10 000"),
            (Decimal("7159"), "7 159"),
            (Decimal(0), "0"),
        ],
    )
    def test_the_amount_is_written_as_on_the_screen(self, value: Decimal, said: str) -> None:
        assert hunter_watch.amount(value) == said


class TestAlarm:
    """Сигнал: уровень — в коде тревоги, упор — словами, повод — по уровню."""

    @pytest.fixture(autouse=True)
    def _cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1000")

    @staticmethod
    async def _spend(session: AsyncSession, units: int) -> None:
        usage.record(session, operation=OPERATION, units=units)
        await session.flush()

    @staticmethod
    async def _alarm(session: AsyncSession, stages: Any = EVERY_STAGE) -> Alarm | None:
        return await hunter_watch.alarm(session, datetime.now(UTC), stages=stages)

    async def test_no_snapshot_and_little_spent_is_quiet(self, session: AsyncSession) -> None:
        await self._spend(session, 799)

        assert await self._alarm(session) is None

    @pytest.mark.parametrize(
        ("spent", "code", "caused"),
        [
            (800, hunter_watch.WARN_CODE, False),
            (950, hunter_watch.ALARM_CODE, True),
            (1000, hunter_watch.REACHED_CODE, True),
            (1200, hunter_watch.REACHED_CODE, True),
        ],
        ids=["80", "95", "весь", "сверх"],
    )
    async def test_the_month_levels(
        self, session: AsyncSession, spent: int, code: str, caused: bool
    ) -> None:
        await self._spend(session, spent)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == code
        assert alarm.stage is Stage.SALES
        assert alarm.concern is not None
        assert alarm.concern.cause is caused
        # Что сделать — в пометке; в подробностях — только у предупреждения: его пометка молчит.
        assert ("поднять потолок" in alarm.detail) is (code == hunter_watch.WARN_CODE)

    @pytest.mark.parametrize(
        ("credits_left", "code", "caused"),
        [
            ("200", hunter_watch.WARN_CODE, False),
            ("50", hunter_watch.ALARM_CODE, True),
            ("0", hunter_watch.REACHED_CODE, True),
        ],
        ids=["80", "95", "весь"],
    )
    async def test_the_account_levels(
        self, session: AsyncSession, credits_left: str, code: str, caused: bool
    ) -> None:
        await remember(session, _account(credits_left=credits_left), at=NOW)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == code
        assert alarm.stage is None, "учётка общая: тревога о ней без этапа"
        assert alarm.concern is not None
        assert alarm.concern.cause is caused

    async def test_a_small_cap_does_not_hide_an_almost_empty_account(
        self, session: AsyncSession
    ) -> None:
        """Потолка 1 000 хватит надолго, а учётка выбрана на 85 %: сказать надо об учётке."""
        await remember(session, _account(credits_left="150", verifications_left="300"), at=NOW)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == hunter_watch.WARN_CODE
        assert "кредитов осталось 150 из 1 000" in alarm.detail
        assert "хватит на 300 проверок" in alarm.detail
        assert "19 ноября" in alarm.detail

    async def test_at_one_level_the_one_that_ends_first_is_named(
        self, session: AsyncSession
    ) -> None:
        await self._spend(session, 810)  # месяц: выбрано 81 %, осталось 190 проверок
        await remember(session, _account(credits_left="100", verifications_left="200"), at=NOW)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == hunter_watch.WARN_CODE
        assert alarm.stage is Stage.SALES, "учётка выбрана больше, но месяц кончится раньше"
        assert "осталось 190 из 1 000" in alarm.detail
        assert "первого числа" in alarm.detail
        assert "учётка обнуляется 19 ноября" in alarm.detail, "две границы — словами"

    async def test_the_higher_level_wins_over_what_ends_first(self, session: AsyncSession) -> None:
        await self._spend(session, 960)  # 96 %, осталось 40
        await remember(session, _account(credits_left="150", verifications_left="20"), at=NOW)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == hunter_watch.ALARM_CODE
        assert alarm.stage is Stage.SALES

    async def test_zero_is_a_human_decision_and_no_alarm(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "0")
        await self._spend(session, 5)

        assert await self._alarm(session) is None

    async def test_words_instead_of_a_cap_leave_the_account_to_speak(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "")
        await self._spend(session, 5000)
        assert await self._alarm(session) is None

        await remember(session, _account(credits_left="0"), at=NOW)

        alarm = await self._alarm(session)
        assert alarm is not None
        assert alarm.code == hunter_watch.REACHED_CODE

    async def test_without_the_sales_right_the_month_is_not_said(
        self, session: AsyncSession
    ) -> None:
        await self._spend(session, 1000)

        assert await self._alarm(session, NO_SALES) is None

    async def test_the_saved_cap_is_the_one_that_counts(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        author = await make_user("потолок@team.example.test", role=UserRole.ADMIN)
        await hunter_cap.set_verify_cap(session, 5000, ceiling=None, author=author)
        await self._spend(session, 1000)

        assert await self._alarm(session) is None

    async def test_without_a_common_pool_the_verifications_speak(
        self, session: AsyncSession
    ) -> None:
        await remember(session, _account(credits_left=None, verifications_left="300"), at=NOW)

        alarm = await self._alarm(session)

        assert alarm is not None
        assert alarm.code == hunter_watch.WARN_CODE
        assert "проверок адреса осталось 300 из 2 000" in alarm.detail

    async def test_the_watch_says_it_among_all_the_rules(self, session: AsyncSession) -> None:
        await self._spend(session, 1000)

        found = await silence.alarms(session, stages=EVERY_STAGE)

        assert hunter_watch.REACHED_CODE in {alarm.code for alarm in found}

    async def test_only_this_month_counts(self, session: AsyncSession) -> None:
        row = usage.record(session, operation=OPERATION, units=1000)
        await session.flush()
        row.created_at = datetime(2026, 10, 31, tzinfo=UTC)
        await session.flush()

        assert await units_this_month(session, OPERATION, now=NOW) == 0


class TestScreen:
    @pytest.fixture(autouse=True)
    def _providers_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_ahrefs(_: AsyncSession) -> tuple[None, str]:
            return None, "нет"

        async def no_serp() -> tuple[None, str]:
            return None, "нет"

        monkeypatch.setattr(settings_routes, "_ahrefs_balance", no_ahrefs)
        monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")

    @pytest.fixture
    async def admin(self, make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
        await make_user("потолок@team.example.test", role=UserRole.ADMIN)
        return bearer(await sign_in("потолок@team.example.test"))

    @staticmethod
    async def _card(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
        answer = await client.get("/api/usage", headers=headers)
        assert answer.status_code == 200, answer.text
        card: dict[str, Any] = answer.json()["hunter"]
        return card

    async def test_no_snapshot_is_said_in_words(
        self, client: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", TEST_KEY)

        card = await self._card(client, admin)

        assert card["account"] is None
        assert card["unknown"].startswith("Hunter ещё не отвечал")

    async def test_no_key_is_said_in_words(
        self, client: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")

        card = await self._card(client, admin)

        assert card["unknown"].startswith("Ключ Hunter на сервере не задан")

    async def test_from_the_wire_to_the_screen(
        self,
        client: AsyncClient,
        admin: dict[str, str],
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ответ Hunter → снимок → чтение экрана: один путь без подмен посередине."""
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", TEST_KEY)
        monkeypatch.setattr(
            hunter_watch, "_http", lambda: _wire(lambda _: httpx.Response(200, json=WIRE))
        )
        await hunter_watch.ask()
        assert await hunter_watch.keep_heard(session)
        await session.commit()
        usage.record(session, operation=OPERATION, units=300)
        await session.commit()

        card = await self._card(client, admin)

        account = card["account"]
        assert card["unknown"] is None
        assert (account["plan"], account["reset_on"]) == ("Team plan", "2026-11-19")
        assert account["credits"] == {"used": 250.5, "available": 1000.0, "remaining": 749.5}
        assert account["verifications"]["remaining"] == 1499
        assert account["used_percent"] == 25
        assert account["asked_at"] is not None
        month = card["month"]
        assert (month["cap"], month["source"], month["spent"]) == (1500, "env", 300)
        assert (month["left"], month["used_percent"], month["most"]) == (1200, 20, 2000)
        assert month["problem"] is None
        assert month["since"].startswith(datetime.now(UTC).strftime("%Y-%m-01"))

    async def test_no_cap_number_comes_with_the_words(
        self, client: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "")

        month = (await self._card(client, admin))["month"]

        assert month["cap"] is None
        assert month["left"] is None
        assert month["problem"] == hunter_cap.NOT_SET
        assert month["most"] == hunter_cap.MOST

    async def test_without_the_sales_right_the_account_stays_and_the_month_goes(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await remember(session, _account(), at=NOW)
        await session.commit()
        await make_user("сторонний@team.example.test", permissions={"sales": False})
        token = await sign_in("сторонний@team.example.test")

        card = await self._card(client, bearer(token))

        assert card["account"]["credits"]["remaining"] == 749.5
        assert card["month"] is None

    async def test_the_admin_saves_and_the_journal_says_was_and_became(
        self, client: AsyncClient, admin: dict[str, str], session: AsyncSession
    ) -> None:
        saved = await client.post(URL, json={"verify_cap": 5000}, headers=admin)

        assert saved.status_code == 200, saved.text
        assert saved.json()["verify_cap"] == 5000
        month = (await self._card(client, admin))["month"]
        assert (month["cap"], month["source"]) == (5000, "setting")
        assert month["changes"][0]["set_by"] == "потолок@team.example.test"
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.HUNTER_CAP_CHANGED)
        )
        assert entry is not None
        assert entry.details is not None
        words = entry.details["потолок проверок адреса"]
        assert words == {
            "было": "как в настройках сервера",
            "стало": "5 000 проверок в месяц",
        }
        assert entry.target == f"hunter_cap:{saved.json()['id']}"

    async def test_zero_stops_the_checks_and_is_a_saved_value(
        self, client: AsyncClient, admin: dict[str, str]
    ) -> None:
        saved = await client.post(URL, json={"verify_cap": 0}, headers=admin)

        assert saved.status_code == 200, saved.text
        month = (await self._card(client, admin))["month"]
        assert (month["cap"], month["left"], month["used_percent"]) == (0, 0, None)

    @pytest.mark.parametrize(
        "value",
        [None, "5000", 1.5, True, -1],
        ids=["пусто", "строка", "дробь", "да-нет", "меньше нуля"],
    )
    async def test_not_a_whole_number_is_refused_in_words(
        self, client: AsyncClient, admin: dict[str, str], value: object
    ) -> None:
        refused = await client.post(URL, json={"verify_cap": value}, headers=admin)

        assert refused.status_code == 422, refused.text
        assert "не меньше нуля" in refused.text

    async def test_above_the_tariff_is_refused_in_words(
        self, client: AsyncClient, admin: dict[str, str], session: AsyncSession
    ) -> None:
        await remember(session, _account(), at=NOW)
        await session.commit()

        refused = await client.post(URL, json={"verify_cap": 2001}, headers=admin)
        allowed = await client.post(URL, json={"verify_cap": 2000}, headers=admin)

        assert refused.status_code == 422, refused.text
        assert "выше проверок тарифа" in refused.json()["detail"]
        assert allowed.status_code == 200, allowed.text

    async def test_an_operator_cannot_change_it(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("оператор@team.example.test")
        token = await sign_in("оператор@team.example.test")

        refused = await client.post(URL, json={"verify_cap": 5000}, headers=bearer(token))

        assert refused.status_code == 403
        assert "менять потолки расхода" in refused.json()["detail"]

    async def test_an_admin_without_the_sales_right_cannot_change_it(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, session: AsyncSession
    ) -> None:
        await make_user(
            "без-продаж@team.example.test", role=UserRole.ADMIN, permissions={"sales": False}
        )
        token = await sign_in("без-продаж@team.example.test")

        refused = await client.post(URL, json={"verify_cap": 5000}, headers=bearer(token))

        assert refused.status_code == 403
        assert "Продажи" in refused.json()["detail"]
        assert await session.scalar(select(func.count()).select_from(HunterCapModel)) == 0


def _names(connection: Connection) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('hunter_caps', 'hunter_account_snapshot')"
        )
    )
    return set(rows.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
    gone = _names(connection)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    return gone, _names(connection)


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    gone, back = await connection.run_sync(_down_and_up)

    assert gone == set()
    assert back == {"hunter_caps", "hunter_account_snapshot"}
    await remember(session, _account(), at=NOW)
    assert await snapshot(session) is not None
