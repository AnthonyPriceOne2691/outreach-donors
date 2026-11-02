"""Панель лимитов: три маршрута только администратору, таблица — настоящие значения, смена — за просчётом.

Требование другой стороны: одна панель на сервис, строки по задачам, новый лимит — с просчётом до
подтверждения, и только для администраторов. Проверяется: без права и без входа все три маршрута
отказывают словами; без права этапа панель без строк этапа; просчёт ничего не пишет и округляет
вниз; смена без просчёта, вне границ и с устаревшим просчётом отказывает, ничего не записав; смена
меняет то же, что старое поле, и пишет тот же журнал; строки равны настройкам и журналу расхода, чего
нет — пусто, а не ноль. Числа — выдуманные: лимиты настоящего ключа в публичный репозиторий не идут.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backend.api.settings import limit_panel
from backend.config import crawl as crawl_cfg
from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.features.access.permissions import no_right
from backend.features.access.repository import AccessRepository
from backend.features.core import llm_caps, usage
from backend.features.core.domain import (
    AuditAction,
    MessageStatus,
    Permission,
    SenderStatus,
    Stage,
    UserRole,
)
from backend.features.core.limit_rows import LimitRefusedError, LimitRow, Quote
from backend.features.core.llm_caps import Block
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import AhrefsCapModel, LlmCapModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.letters.chain import FIRST_STEP
from backend.features.ops import funds_watch, limit_model
from backend.features.ops.limit_fields import BLANK, SAME
from backend.features.runs.ahrefs_cap import chosen_cap
from backend.features.sales.segment_spend import usd_per_million
from backend.features.sales.usage_cap import OPERATIONS as SALES_OPERATIONS
from backend.features.sales.usage_cap import sales_cap
from backend.features.serp.funds import dollars
from backend.shared.numbers import spaced
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_ahrefs_cap import KEY, _waiting_for_a_lock, known_key
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

PANEL = "/api/usage/limits"
QUOTE = "/api/usage/limits/quote"
CHANGE = "/api/usage/limits/change"
M = 1_000_000
#: Блок после первого сохранения: общий 50 млн, продажи 20 %, агент 20 %, аутрич 30 %, свободно 30 %.
SAVED = Block(50 * M, 20, 20, 30)
SALES_DAY = {"key": "sales.llm", "field": "per_day"}
AHREFS = {"key": "outreach.ahrefs.units", "field": "per_month"}


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user("панель@team.example.test", role=UserRole.ADMIN)


@pytest.fixture
async def token(admin: UserModel, sign_in: SignIn) -> str:
    return await sign_in(admin.email)


@pytest.fixture
def env_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настройки сервера, как на проде: общего нет, продаж — 10 млн, агента — пусто."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 10 * M)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", None)


async def _saved(session: AsyncSession, author: UserModel, block: Block = SAVED) -> None:
    await llm_caps.set_llm_caps(session, block, author=author)
    await session.commit()
    await llm_caps.refresh(session)


async def _panel(client: AsyncClient, token: str) -> dict[str, dict[str, Any]]:
    answer = await client.get(PANEL, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    return {row["key"]: row for row in answer.json()["rows"]}


async def _written(session: AsyncSession) -> tuple[int, int, int]:
    """Сколько строк истории и журнала: просчёт и отказ их не прибавляют."""
    return (
        int(await session.scalar(select(func.count()).select_from(LlmCapModel)) or 0),
        int(await session.scalar(select(func.count()).select_from(AhrefsCapModel)) or 0),
        int(await session.scalar(select(func.count()).select_from(AuditLogModel)) or 0),
    )


class TestAccess:
    ROUTES = (
        ("GET", PANEL, None),
        ("POST", QUOTE, {**SALES_DAY, "value": 15 * M}),
        ("POST", CHANGE, {**SALES_DAY, "value": 15 * M, "quoted": 15 * M}),
    )

    @pytest.mark.parametrize(("method", "url", "body"), ROUTES)
    async def test_nobody_without_signing_in(
        self, client: AsyncClient, method: str, url: str, body: dict[str, Any] | None
    ) -> None:
        answer = await client.request(method, url, json=body)

        assert answer.status_code == 403
        assert answer.json()["detail"]

    @pytest.mark.parametrize(("method", "url", "body"), ROUTES)
    async def test_an_operator_is_refused_in_words(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        method: str,
        url: str,
        body: dict[str, Any] | None,
    ) -> None:
        operator = await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
        token = await sign_in(operator.email)

        answer = await client.request(method, url, json=body, headers=bearer(token))

        assert answer.status_code == 403
        assert answer.json()["detail"] == no_right(Permission.SPEND)

    async def test_the_right_opens_the_panel_not_the_role(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        operator = await make_user(
            "выдано@team.example.test", role=UserRole.OPERATOR, permissions={"spend": True}
        )

        rows = await _panel(client, await sign_in(operator.email))

        assert "outreach.ahrefs.units" in rows

    async def test_without_the_sales_right_the_panel_has_no_sales(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """П2б: и строки продаж, и общий потолок — его выбранное минус две части выдало бы продажи."""
        admin = await make_user(
            "без-продаж@team.example.test", role=UserRole.ADMIN, permissions={"sales": False}
        )
        token = await sign_in(admin.email)

        rows = await _panel(client, token)
        refused = await client.post(
            QUOTE, json={**SALES_DAY, "value": 15 * M}, headers=bearer(token)
        )

        assert not [key for key in rows if key.startswith("sales.")]
        assert "outreach.llm.general" not in rows
        assert "outreach.llm" in rows
        assert refused.status_code == 403
        assert "«Продажи»" in refused.json()["detail"]

    async def test_without_the_advertisers_right_the_panel_has_no_stage_two(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Расход Этапа 2 и его потолок — только с правом «Рекламодатели», как на «Расходе»."""
        admin = await make_user(
            "без-этапа@team.example.test", role=UserRole.ADMIN, permissions={"advertisers": False}
        )
        token = await sign_in(admin.email)

        rows = await _panel(client, token)
        refused = await client.post(
            QUOTE,
            json={"key": "outreach.advertisers.crawl.paid", "field": "per_month", "value": 5},
            headers=bearer(token),
        )

        assert not [key for key in rows if key.startswith("outreach.advertisers.")]
        assert "outreach.donors.mail.first" in rows
        assert refused.status_code == 403
        assert "«Рекламодатели»" in refused.json()["detail"]

    async def test_the_ahrefs_cap_keeps_its_own_right(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Карточка капа — правом «править пороги»: панель этого права не обходит."""
        admin = await make_user(
            "без-порогов@team.example.test", role=UserRole.ADMIN, permissions={"settings": False}
        )
        await known_key(session)
        await session.commit()

        answer = await client.post(
            QUOTE, json={**AHREFS, "value": 1000}, headers=bearer(await sign_in(admin.email))
        )

        assert answer.status_code == 403
        assert answer.json()["detail"] == no_right(Permission.SETTINGS)


@pytest.mark.usefixtures("env_caps")
class TestRows:
    async def test_keys_are_unique_and_tasks_go_in_order(
        self, client: AsyncClient, token: str
    ) -> None:
        answer = await client.get(PANEL, headers=bearer(token))
        rows = answer.json()["rows"]

        keys = [row["key"] for row in rows]
        assert len(keys) == len(set(keys))
        tasks = [row["task"] for row in rows]
        assert tasks == sorted(tasks, key=limit_panel.TASKS.index)

    async def test_a_row_without_editing_says_why(self, client: AsyncClient, token: str) -> None:
        rows = await _panel(client, token)

        assert all(row["now"] for row in rows.values() if not row["editable"])

    async def test_the_model_before_saving_is_the_env(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        usage.record(session, operation="sales_reply_kind", units=300)
        usage.record(session, operation="agent_draft", units=200)
        usage.record(session, operation="site_judge", units=100)
        await session.commit()

        rows = await _panel(client, token)

        sales, agent, outreach = (
            rows["sales.llm"],
            rows["outreach.agent.llm"],
            rows["outreach.llm"],
        )
        assert (sales["task"], sales["unit"]) == ("Продажи", "токенов")
        assert (sales["per_day"], sales["used_day"], sales["used_month"]) == (10 * M, 300, 300)
        assert (agent["per_day"], agent["used_day"]) == (None, 200)
        assert (outreach["per_day"], outreach["used_day"]) == (None, 100)
        assert sales["editable"] == []
        assert limit_model.NOT_SAVED in sales["now"]
        assert sales["source"] == ".env SALES_DAILY_TOKEN_CAP"
        assert rows["outreach.llm.general"]["used_day"] == 600

    async def test_an_unread_setting_is_said_in_the_row(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({"SALES_DAILY_TOKEN_CAP"}))

        rows = await _panel(client, token)

        assert "не прочитана" in rows["sales.llm"]["now"]
        assert limit_model.NOT_SAVED in rows["sales.llm"]["now"]

    async def test_the_sales_part_counts_every_sales_call(self) -> None:
        assert usage.PART_OPERATIONS[llm_caps.PART_SALES] == SALES_OPERATIONS

    async def test_the_model_after_saving_is_the_block(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        rows = await _panel(client, token)

        assert rows["sales.llm"]["per_day"] == SAVED.limit(llm_caps.PART_SALES)
        assert rows["outreach.agent.llm"]["per_day"] == SAVED.limit(llm_caps.PART_AGENT)
        assert rows["outreach.llm"]["per_day"] == SAVED.limit(llm_caps.PART_OUTREACH)
        assert rows["outreach.llm.general"]["per_day"] == 50 * M
        assert rows["sales.llm"]["editable"] == ["per_day"]
        assert rows["sales.llm"]["source"] == limit_model.FROM_SCREEN
        assert "20 % общего" in rows["sales.llm"]["now"]

    async def test_a_cost_is_an_estimate_in_words_and_nothing_without_a_limit(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        rows = await _panel(client, token)

        tokens = SAVED.limit(llm_caps.PART_SALES) * 30
        expected = Decimal(str(round(tokens * usd_per_million() / M, 4))).quantize(Decimal("0.01"))
        assert Decimal(rows["sales.llm"]["cost_usd"]) == expected
        assert "Оценка снизу" in rows["sales.llm"]["cost_note"]

    async def test_without_a_general_the_parts_do_not_change(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin, Block(None, 20, 20, 30))

        rows = await _panel(client, token)

        assert (rows["sales.llm"]["per_day"], rows["sales.llm"]["editable"]) == (None, [])
        assert rows["sales.llm"]["cost_usd"] is None
        assert rows["sales.llm"]["now"] == limit_model.NO_GENERAL
        assert rows["outreach.llm.general"]["editable"] == ["per_day"]

    async def test_mail_rows_are_the_real_settings(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", 70)
        monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
        box = SenderModel(
            domain="mail-a.example.test",
            email="box@mail-a.example.test",
            stage=Stage.SALES,
            daily_cap=20,
            status=SenderStatus.FREE,
            enabled=True,
        )
        campaign = CampaignModel(stage=Stage.SALES, name="Проверка", status="running")
        session.add_all(
            [
                box,
                campaign,
                SendingDomainModel(domain="mail-a.example.test", stage=Stage.SALES, daily_limit=40),
            ]
        )
        await session.flush()
        domain = await make_donor(session, "first.example.test")
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=domain.id,
                sender_id=box.id,
                step=FIRST_STEP,
                status=MessageStatus.SENT,
                sent_at=datetime.now(UTC),
                idempotency_key="sales:first.example.test:0",
            )
        )
        await session.commit()

        rows = await _panel(client, token)

        first, one, chain = (
            rows["sales.mail.first"],
            rows["sales.mail.box"],
            rows["sales.mail.followup"],
        )
        assert (first["per_day"], first["used_day"], first["used_month"]) == (70, 1, 1)
        assert first["source"] == ".env OUTREACH_SALES_DAILY_LIMIT"
        assert "лимит 40 в день" in first["now"]
        assert first["cost_usd"] is None
        assert "тарифом" in first["cost_note"]
        assert one["per_day"] == outreach_cfg.DAILY_CAP_PER_SENDER
        assert (one["per_session"], one["session_hours"]) == (
            60 // sales_cfg.AUTOSEND_GAP_MIN,
            1,
        )
        assert one["used_day"] == 1
        assert (chain["per_session"], chain["session_hours"], chain["per_day"]) == (
            outreach_cfg.FOLLOWUP_PER_SENDER_PER_HOUR,
            1,
            None,
        )
        assert first["editable"] == one["editable"] == chain["editable"] == []

    async def test_mail_without_a_direction_limit_is_empty_not_zero(
        self, client: AsyncClient, token: str
    ) -> None:
        rows = await _panel(client, token)

        donors = rows["outreach.donors.mail.first"]
        assert donors["per_day"] is None
        assert donors["task"] == "Аутрич"
        assert "Лимита направления нет" in donors["now"]

    async def test_without_autosend_a_box_has_no_hour(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sales_cfg, "AUTOSEND", False)

        rows = await _panel(client, token)

        assert rows["sales.mail.box"]["per_session"] is None
        assert "Автоотправка выключена" in rows["sales.mail.box"]["now"]
        # Включённых ящиков нет: сокращение не дописывает к точке предложения вторую.
        assert ".." not in rows["sales.mail.box"]["now"]

    async def test_services_are_read_only_and_real(
        self,
        client: AsyncClient,
        session: AsyncSession,
        token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 40.0)
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 2 * M)
        usage.record(session, operation="serp_search", amount_usd=Decimal("1.25"))
        usage.record(session, operation="contacts_search", units=3, stage=Stage.DONORS)
        usage.record(session, operation="contacts_search", units=9, stage=Stage.SALES)
        usage.record(session, operation="crawl_render", units=10, amount_usd=Decimal("0.5"))
        await session.commit()

        rows = await _panel(client, token)

        paid = rows["outreach.advertisers.crawl.paid"]
        assert (paid["per_month"], paid["unit"]) == (40, "$")
        assert (Decimal(paid["cost_usd"]), Decimal(paid["spent_month_usd"])) == (
            Decimal(40),
            Decimal("0.5"),
        )
        serp = rows["outreach.donors.serp.buy"]
        assert serp["per_session"] == serp_cfg.MAX_KEYWORDS_PER_RUN
        assert Decimal(serp["spent_month_usd"]) == Decimal("1.25")
        hunter = rows["outreach.hunter.search"]
        assert (hunter["used_day"], hunter["used_month"], hunter["per_month"]) == (3, 3, None)
        deep = rows["outreach.advertisers.crawl.deep"]
        assert deep["per_session"] == crawl_cfg.MAX_PAGES_DEEP
        assert deep["session_hours"] == 16
        assert f"не больше {crawl_cfg.DEEP_MAX_RUNNING}" in deep["now"]
        assert rows["outreach.llm.run"]["per_session"] == 2 * M
        changing = ("llm", "ahrefs", "sales.hunter.verify")
        assert all(
            rows[key]["editable"] == [] for key in rows if not any(one in key for one in changing)
        )

    async def test_the_serp_balance_is_what_the_watch_heard(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unheard = (await _panel(client, token))["outreach.donors.serp.buy"]
        monkeypatch.setattr(funds_watch.SEEN, "balance", 42.5)

        heard = (await _panel(client, token))["outreach.donors.serp.buy"]

        assert "по последнему опросу" not in unheard["now"]
        assert f"На счету {dollars(42.5)} по последнему опросу" in heard["now"]

    async def test_a_cap_without_a_whole_number_stays_in_the_cost(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 12.5)

        paid = (await _panel(client, token))["outreach.advertisers.crawl.paid"]

        assert paid["per_month"] is None
        assert Decimal(paid["cost_usd"]) == Decimal("12.5")

    async def test_no_crawl_cap_says_the_levels_are_not_called(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", None)

        paid = (await _panel(client, token))["outreach.advertisers.crawl.paid"]

        assert (paid["per_month"], paid["cost_usd"]) == (None, None)
        assert "не зовутся" in paid["now"]

    async def test_the_ahrefs_row_is_the_key_cap(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await known_key(session)
        usage.record(session, operation="batch_metrics", units=1200, stage=Stage.DONORS)
        await session.commit()

        row = (await _panel(client, token))["outreach.ahrefs.units"]

        assert (row["per_month"], row["used_month"], row["used_day"]) == (KEY, 1200, 1200)
        assert row["editable"] == ["per_month"]
        assert row["cost_usd"] is None
        assert "тарифом" in row["cost_note"]
        assert row["source"] == "снимок ключа Ahrefs"

    async def test_an_unknown_key_cannot_be_changed_and_says_why(
        self, client: AsyncClient, token: str
    ) -> None:
        row = (await _panel(client, token))["outreach.ahrefs.units"]

        assert (row["per_month"], row["editable"]) == (None, [])
        assert "Кап неизвестен" in row["now"]


async def _quote(client: AsyncClient, token: str, value: int, base: dict[str, str]) -> Any:
    answer = await client.post(QUOTE, json={**base, "value": value}, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    return answer.json()


@pytest.mark.usefixtures("env_caps")
class TestQuote:
    async def test_it_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        await known_key(session)
        await session.commit()
        before = await _written(session)

        share = await _quote(client, token, 15 * M, SALES_DAY)
        cap = await _quote(client, token, 1000, AHREFS)

        assert (share["refusal"], cap["refusal"]) == (None, None)
        assert await _written(session) == before
        assert llm_caps.block() == SAVED
        assert await chosen_cap(session) is None

    async def test_tokens_round_down_to_the_step(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        quoted = await _quote(client, token, 17 * M, SALES_DAY)

        assert quoted["refusal"] is None
        assert quoted["accepted"] == 15 * M
        assert "Округлено вниз" in quoted["words"]
        assert "30 % общего" in quoted["words"]

    async def test_what_rounds_to_the_current_limit_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        quoted = await _quote(client, token, 12 * M, SALES_DAY)

        assert quoted["refusal"] == SAME

    async def test_the_cost_follows_the_new_limit(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        quoted = await _quote(client, token, 20 * M, SALES_DAY)

        tokens = 20 * M * 30
        assert Decimal(quoted["cost_usd"]) == Decimal(
            str(round(tokens * usd_per_million() / M, 4))
        ).quantize(Decimal("0.01"))

    @pytest.mark.parametrize(
        ("value", "said"),
        [
            (0, "не меньше 5 %"),
            (-1, "не меньше 5 %"),
            (2 * M, "не меньше 5 %"),
            (28 * M, "Свободно 30 %"),
        ],
    )
    async def test_out_of_bounds_is_refused_in_words(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: UserModel,
        token: str,
        value: int,
        said: str,
    ) -> None:
        await _saved(session, admin)

        quoted = await _quote(client, token, value, SALES_DAY)

        assert said in quoted["refusal"]
        assert quoted["accepted"] == 0

    async def test_the_general_takes_only_the_list(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        general = {"key": "outreach.llm.general", "field": "per_day"}

        offlist = await _quote(client, token, 55 * M, general)
        listed = await _quote(client, token, 100 * M, general)

        assert "одно из чисел списка" in offlist["refusal"]
        assert (listed["refusal"], listed["accepted"]) == (None, 100 * M)
        assert "продажи 20" in listed["words"].replace(" ", "")

    async def test_the_ahrefs_cap_stays_in_units(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await known_key(session)
        usage.record(session, operation="batch_metrics", units=1200, stage=Stage.DONORS)
        await session.commit()

        fits = await _quote(client, token, 5000, AHREFS)
        above = await _quote(client, token, KEY + 1, AHREFS)
        spent = await _quote(client, token, 1000, AHREFS)

        assert (fits["accepted"], fits["cost_usd"]) == (5000, None)
        assert "тарифом" in fits["words"]
        assert "не годится" in above["refusal"]
        assert "кап уже выбран" in spent["words"]

    async def test_a_read_only_row_refuses_with_its_own_words(
        self, client: AsyncClient, token: str
    ) -> None:
        answer = await client.post(
            QUOTE,
            json={"key": "sales.mail.first", "field": "per_day", "value": 100},
            headers=bearer(token),
        )

        assert answer.status_code == 422
        assert "с экрана не меняется" in answer.json()["detail"]

    async def test_a_saved_row_refuses_a_field_it_does_not_have(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)

        answer = await client.post(
            QUOTE,
            json={"key": "sales.llm", "field": "per_month", "value": 100},
            headers=bearer(token),
        )

        assert answer.status_code == 422
        assert "20 % общего" in answer.json()["detail"]

    @pytest.mark.parametrize("value", ["5000", 1.5, True, None])
    async def test_a_value_is_a_whole_number(
        self, client: AsyncClient, token: str, value: object
    ) -> None:
        answer = await client.post(QUOTE, json={**SALES_DAY, "value": value}, headers=bearer(token))

        assert answer.status_code == 422
        assert answer.json()["detail"][0]["msg"] == "Лимит — целое число"


async def _change(client: AsyncClient, token: str, body: dict[str, Any]) -> Any:
    return await client.post(CHANGE, json=body, headers=bearer(token))


@pytest.mark.usefixtures("env_caps")
class TestChange:
    async def test_without_a_quote_nothing_changes(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        before = await _written(session)

        answer = await _change(client, token, {**SALES_DAY, "value": 15 * M})

        assert answer.status_code == 422
        assert answer.json()["detail"] == limit_panel.NOT_QUOTED
        assert await _written(session) == before

    async def test_out_of_bounds_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        before = await _written(session)

        answer = await _change(client, token, {**SALES_DAY, "value": 49 * M, "quoted": 49 * M})

        assert answer.status_code == 422
        assert "Свободно 30 %" in answer.json()["detail"]
        assert await _written(session) == before
        assert llm_caps.block() == SAVED

    async def test_it_changes_what_the_old_field_changes_and_writes_the_journal(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        quoted = await _quote(client, token, 15 * M, SALES_DAY)

        answer = await _change(
            client, token, {**SALES_DAY, "value": 15 * M, "quoted": quoted["accepted"]}
        )

        assert answer.status_code == 200, answer.text
        wanted = Block(50 * M, 30, 20, 30)
        assert llm_caps.block() == wanted  # память API — сразу
        assert sales_cap().tokens == 15 * M
        last = await session.scalar(select(LlmCapModel).order_by(LlmCapModel.id.desc()).limit(1))
        assert last is not None
        assert (last.sales_share, last.set_by) == (30, admin.id)
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.LLM_CAP_CHANGED)
        )
        assert entry is not None
        assert entry.user_id == admin.id
        assert entry.target == f"llm_caps:{last.id}"
        assert entry.details == {
            "потолки модели": {
                "было": llm_caps.block_words(SAVED),
                "стало": llm_caps.block_words(wanted),
            }
        }
        rows = await _panel(client, token)
        assert rows["sales.llm"]["per_day"] == 15 * M

    async def test_a_stale_quote_that_became_invalid_is_refused(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        quoted = await _quote(client, token, 25 * M, SALES_DAY)
        assert quoted["refusal"] is None
        await _saved(session, admin, Block(50 * M, 20, 45, 30))  # свободный остаток разошёлся
        before = await _written(session)

        answer = await _change(
            client, token, {**SALES_DAY, "value": 25 * M, "quoted": quoted["accepted"]}
        )

        assert answer.status_code == 422
        assert "Свободно 5 %" in answer.json()["detail"]
        assert await _written(session) == before

    async def test_a_stale_quote_with_another_number_asks_to_quote_again(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        """Общий подвинули: те же токены — другой шаг, и встало бы не то, что видел человек."""
        await _saved(session, admin)
        quoted = await _quote(client, token, 12_600_000, SALES_DAY)
        assert quoted["accepted"] == 12_500_000
        await _saved(session, admin, Block(30 * M, 20, 20, 30))
        before = await _written(session)

        answer = await _change(
            client, token, {**SALES_DAY, "value": 12_600_000, "quoted": quoted["accepted"]}
        )

        assert answer.status_code == 422
        assert answer.json()["detail"] == (
            f"Просчёт устарел: теперь подтвердится {spaced(12 * M)}, а не {spaced(12_500_000)} — "
            "просчитайте заново"
        )
        assert await _written(session) == before

    async def test_a_stale_quote_that_still_fits_goes_through(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        quoted = await _quote(client, token, 15 * M, SALES_DAY)
        await _saved(session, admin, Block(50 * M, 20, 25, 30))

        answer = await _change(
            client, token, {**SALES_DAY, "value": 15 * M, "quoted": quoted["accepted"]}
        )

        assert answer.status_code == 200, answer.text
        assert llm_caps.block() == Block(50 * M, 30, 25, 30)

    async def test_the_general_moves_the_parts_with_it(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await _saved(session, admin)
        general = {"key": "outreach.llm.general", "field": "per_day"}

        answer = await _change(client, token, {**general, "value": 100 * M, "quoted": 100 * M})

        assert answer.status_code == 200, answer.text
        assert llm_caps.block() == Block(100 * M, 20, 20, 30)
        assert sales_cap().tokens == 20 * M

    async def test_the_ahrefs_cap_goes_the_existing_way(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        await known_key(session)
        await session.commit()

        answer = await _change(client, token, {**AHREFS, "value": 5000, "quoted": 5000})

        assert answer.status_code == 200, answer.text
        assert await chosen_cap(session) == 5000
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.AHREFS_CAP_CHANGED)
        )
        assert entry is not None
        assert entry.details == {
            "кап Ahrefs": {"было": "весь лимит ключа", "стало": 5000},
            "лимит ключа": KEY,
        }
        rows = await _panel(client, token)
        assert rows["outreach.ahrefs.units"]["source"] == "поле: вкладка «Лимиты»"

    async def test_the_old_field_writes_the_same_journal(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, token: str
    ) -> None:
        """Запись журнала вынесена из маршрута карточки — и у него осталась прежней."""
        await known_key(session)
        await session.commit()

        answer = await client.post(
            "/api/usage/ahrefs-cap", json={"units_cap": 7000}, headers=bearer(token)
        )

        assert answer.status_code == 200, answer.text
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.AHREFS_CAP_CHANGED)
        )
        assert entry is not None
        assert entry.details == {
            "кап Ahrefs": {"было": "весь лимит ключа", "стало": 7000},
            "лимит ключа": KEY,
        }


async def test_two_changes_at_once_do_not_overwrite_each_other() -> None:
    """Панель меняет одно число, а сеттер пишет блок целиком: без замка вторая смена прочла бы блок
    до фиксации первой и вернула бы её часть назад. Замок берётся до чтения блока."""
    async with committed_sessions() as factory:
        async with factory() as setup:
            author = await AccessRepository(setup).create(
                email="гонка@team.example.test", password="пароль-для-теста", role=UserRole.ADMIN
            )
            await llm_caps.set_llm_caps(setup, SAVED, author=author)
            await setup.commit()

        async with factory() as first, factory() as second:
            await limit_model.change(first, "sales.llm", "per_day", 15 * M, author)
            later = asyncio.create_task(
                limit_model.change(second, "outreach.agent.llm", "per_day", 15 * M, author)
            )
            for _ in range(100):
                if await _waiting_for_a_lock(factory):
                    break
                await asyncio.sleep(0.05)
            else:
                later.cancel()
                pytest.fail("вторая смена не ждала замка первой")
            assert not later.done()
            await first.commit()
            await asyncio.wait_for(later, timeout=10)
            await second.commit()

        async with factory() as check:
            await llm_caps.refresh(check)

    assert llm_caps.block() == Block(50 * M, 30, 30, 30)


class TestAssembly:
    def test_a_stage_is_read_from_the_key(self) -> None:
        assert limit_panel.stage_of("sales.hunter.verify") is Stage.SALES
        assert limit_panel.stage_of("outreach.advertisers.crawl.deep") is Stage.ADVERTISERS
        assert limit_panel.stage_of("outreach.donors.serp.buy") is Stage.DONORS
        assert limit_panel.stage_of("outreach.ahrefs.units") is None

    async def test_a_new_source_is_one_line(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Модуль продаж встаёт одной строкой списка: сборка берёт его строки, зовёт его просчёт
        и его смену, ничего о нём не зная, и строки продаж идут первыми."""
        row = replace(_row("sales.hunter.verify", "Продажи"), editable=frozenset({"per_month"}))
        calls: list[str] = []

        class _Sales:
            async def rows(self, _session: AsyncSession) -> list[LimitRow]:
                return [row]

            async def quote(self, _session: AsyncSession, *_args: object) -> Quote:
                calls.append("quote")
                return Quote(None, "слова", None, 7)

            async def change(self, _session: AsyncSession, *_args: object) -> None:
                calls.append("change")

        monkeypatch.setattr(limit_panel, "EDITABLE", (limit_model, _Sales()))

        found = await limit_panel.rows(session)
        done = await limit_panel.change(session, row.key, "per_month", 7, 7, _nobody())

        assert found[0].task == "Продажи"
        assert row in found
        assert done.accepted == 7
        assert calls == ["quote", "change"]

    async def test_a_source_that_refuses_writes_nothing(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = replace(_row("sales.hunter.verify", "Продажи"), editable=frozenset({"per_month"}))
        calls: list[str] = []

        class _Sales:
            async def rows(self, _session: AsyncSession) -> list[LimitRow]:
                return [row]

            async def quote(self, _session: AsyncSession, *_args: object) -> Quote:
                return Quote(None, "нельзя", "нельзя", 0)

            async def change(self, _session: AsyncSession, *_args: object) -> None:
                calls.append("change")

        monkeypatch.setattr(limit_panel, "EDITABLE", (_Sales(),))

        with pytest.raises(LimitRefusedError, match="нельзя"):
            await limit_panel.change(session, row.key, "per_month", 7, 7, _nobody())

        assert calls == []

    def test_a_row_of_a_stage_is_hidden_from_who_cannot_see_it(self) -> None:
        rows = [_row("sales.llm", "Продажи"), _row("outreach.ahrefs.units", "Аутрич")]

        assert limit_panel.visible(rows, {Stage.DONORS}) == [rows[1]]
        assert limit_panel.visible(rows, set(Stage)) == rows


def _row(key: str, task: str) -> LimitRow:
    return replace(BLANK, key=key, task=task)


def _nobody() -> UserModel:
    """Автор смены для подставного источника: он его не читает."""
    return UserModel(id=0, email="никто@team.example.test")
