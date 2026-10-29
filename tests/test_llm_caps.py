"""Потолки модели — с экрана «Расход» (слово Anthony 29.10.2026: «чтобы администратор мог
поднимать расходы сам»; устройство — как ключи в пространстве Ahrefs: общий потолок сервиса и у
каждой части свой лимит, процентом общего, а свободный остаток раздают руками).

Проверяется: блок не сохраняли — потолки из `.env`, как были; сохранили — у продаж, агента
переписки и аутрича лимит процентом общего, жёсткий: потраченное частью не трогает другие, а
свободный остаток никто не тратит; общий двигают — лимиты растут вместе с ним; «без потолка» —
отказов нет, доли запомнены; сторож, смета сбора продаж (без правки модуля продаж) и смета прогона
по конкурентам видят одно и то же; черновик продаж судит лимит продаж, а не агента; общий не из
списка, лимит не шагом и розданное сверх 100 % — отказ словами до записи; смена — строкой истории
и журнала «было → стало»; менять — правом «менять потолки расхода» (у оператора его нет), лимит
продаж — ещё и правом «Продажи»; отказ называет раздел, что делать и сброс «через N ч M мин (в
03:00 по Москве)»; ревизия ходит вниз и вверх. Числа — выдуманные.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.settings import routes as settings_routes
from backend.config import llm as llm_cfg
from backend.features.agent import guarding
from backend.features.agent.guarding import drafts_cap
from backend.features.core import llm_caps, usage
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.llm_caps import Block, LlmCapError
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import LlmCapModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.runs import competitor_estimate
from backend.features.sales import segment_estimate
from backend.features.sales.usage_cap import sales_cap
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/usage/llm-caps"
MIGRATION = "fdaa566c5a26_llm_caps.py"
#: Минута проверки: до полуночи UTC — 5 ч 12 мин, по Москве сброс в 03:00.
EVENING = datetime(2026, 10, 29, 18, 48, tzinfo=UTC)
M = 1_000_000
#: Предложенное при первом открытии: общий 50 млн, продажи 20 %, агент 20 %, аутрич 30 %.
SUGGESTED = Block(50 * M, 20, 20, 30)


async def _spend(session: AsyncSession, operation: str, tokens: int) -> None:
    usage.record(session, operation=operation, units=tokens)
    await session.flush()


async def _save(session: AsyncSession, author: UserModel, wanted: Block) -> llm_caps.LlmCapSet:
    done = await llm_caps.set_llm_caps(session, wanted, author=author)
    await llm_caps.refresh(session)
    return done


def _body(wanted: Block) -> dict[str, int | None]:
    return {
        "general_tokens": wanted.general,
        "sales_share": wanted.sales,
        "agent_share": wanted.agent,
        "outreach_share": wanted.outreach,
    }


@pytest.fixture
async def admin(make_user: MakeUser) -> UserModel:
    return await make_user("потолок@team.example.test", role=UserRole.ADMIN)


@pytest.fixture
def env_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настройки сервера, как на проде: общего нет, продаж — 10 млн, агента — пусто."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 10 * M)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", None)


@pytest.mark.usefixtures("env_caps")
class TestEnvUntilSaved:
    async def test_not_saved_the_env_acts(self, session: AsyncSession) -> None:
        await llm_caps.refresh(session)

        assert llm_caps.block() is None
        assert llm_caps.daily_cap() == 0
        assert sales_cap().tokens == 10 * M
        assert drafts_cap().tokens == 0  # доля общего, а общего нет

    async def test_the_env_sales_cap_stops_with_the_reset_in_words(
        self, session: AsyncSession
    ) -> None:
        await _spend(session, "sales_reply_kind", 10 * M)

        with pytest.raises(LlmCapExceededError) as refused:
            await usage.ensure_llm_within_cap(session, own=sales_cap(), now=EVENING)

        assert str(refused.value) == (
            f"дневной потолок вызовов модели продаж выбран: {10 * M} из {10 * M} токенов — "
            "попросите администратора поднять потолок на экране «Расход» или дождитесь сброса — "
            "через 5 ч 12 мин (в 03:00 по Москве)"
        )


@pytest.mark.usefixtures("env_caps")
class TestParts:
    async def test_each_part_gets_its_share_of_the_general(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _save(session, admin, SUGGESTED)

        assert llm_caps.daily_cap() == 50 * M
        assert (sales_cap().tokens, sales_cap().declared) == (10 * M, 10 * M)
        assert drafts_cap().tokens == 10 * M
        saved = llm_caps.block()
        assert saved is not None
        assert (saved.limit(llm_caps.PART_OUTREACH), saved.free_share, saved.free) == (
            15 * M,
            30,
            15 * M,
        )

    async def test_a_part_limit_is_hard_and_names_the_part(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _save(session, admin, SUGGESTED)
        await _spend(session, "sales_segment_judge", 10 * M)

        with pytest.raises(LlmCapExceededError) as refused:
            await usage.ensure_llm_within_cap(session, own=sales_cap(), now=EVENING)

        assert str(refused.value) == (
            f"лимит раздела «Продажи» на сегодня выбран: {10 * M} из {10 * M} токенов — "
            "попросите администратора добавить разделу из свободного остатка или поднять общий "
            "потолок на экране «Расход», или дождитесь сброса — через 5 ч 12 мин (в 03:00 по "
            "Москве)"
        )

    async def test_others_and_the_free_rest_do_not_flow_in(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Продажи выбрали свой лимит — агент и аутрич работают, а свободный остаток продажам
        сам не достаётся."""
        await _save(session, admin, SUGGESTED)
        await _spend(session, "sales_draft", 10 * M)

        await usage.ensure_llm_within_cap(session, own=drafts_cap())
        await usage.ensure_llm_within_cap(session)  # без своего потолка — аутрич
        with pytest.raises(LlmCapExceededError, match="«Продажи»"):
            await usage.ensure_llm_within_cap(session, own=sales_cap())

    async def test_without_an_own_cap_the_call_is_outreach(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _save(session, admin, SUGGESTED)
        await _spend(session, "reply_parse", 15 * M)

        with pytest.raises(LlmCapExceededError, match="лимит раздела «Аутрич»"):
            await usage.ensure_llm_within_cap(session)
        await usage.ensure_llm_within_cap(session, own=sales_cap())

    async def test_the_general_moves_the_limits_with_it(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _save(session, admin, SUGGESTED)
        await _save(session, admin, Block(100 * M, 20, 20, 30))

        assert sales_cap().tokens == 20 * M
        assert drafts_cap().tokens == 20 * M

    async def test_no_cap_lifts_every_part_and_remembers_the_shares(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _save(session, admin, Block(None, 25, 20, 30))
        await _spend(session, "sales_draft", 60 * M)
        await _spend(session, "reply_parse", 60 * M)

        await usage.ensure_llm_within_cap(session, own=sales_cap())
        await usage.ensure_llm_within_cap(session)
        assert sales_cap().tokens == 0
        assert await segment_estimate.tokens_left(session) is None
        assert llm_caps.block() == Block(None, 25, 20, 30)

    async def test_the_sales_estimate_counts_from_the_sales_limit(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Случай с прода: смета сбора продаж — 1,77 млн токенов при остатке 112 767. Лимит
        продаж поднят на экране — остаток сметы считается от него, без правки модуля продаж."""
        await _spend(session, "sales_segment_judge", 9_887_233)
        assert await segment_estimate.tokens_left(session) == 112_767

        await _save(session, admin, Block(100 * M, 20, 20, 30))

        assert await segment_estimate.tokens_left(session) == 20 * M - 9_887_233

    async def test_a_stale_memory_is_read_again_before_the_check(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        """Потолок, построенный до смены в другом процессе, сторож перечитывает сам."""
        stale = sales_cap()
        await llm_caps.set_llm_caps(session, Block(10 * M, 5, 20, 30), author=admin)
        await _spend(session, "sales_draft", M)

        assert stale.tokens == 10 * M
        with pytest.raises(LlmCapExceededError, match="«Продажи»"):
            await usage.ensure_llm_within_cap(session, own=stale)

    async def test_a_saved_block_beats_an_unread_env_setting(
        self, session: AsyncSession, admin: UserModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({llm_caps.SALES}))
        with pytest.raises(LlmCapExceededError, match="не прочитан из настроек"):
            await usage.ensure_llm_within_cap(session, own=sales_cap())

        await _save(session, admin, SUGGESTED)

        await usage.ensure_llm_within_cap(session, own=sales_cap())

    async def test_the_competitor_estimate_counts_from_the_outreach_limit(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        await _spend(session, "site_judge", 14 * M)
        assert await competitor_estimate.llm_refusal(session, 2 * M) is None

        await _save(session, admin, SUGGESTED)

        said = await competitor_estimate.llm_refusal(session, 2 * M)
        assert said is not None
        assert "остаток лимита раздела «Аутрич» — 1 000 000" in said
        assert "добавить разделу из свободного остатка" in said

    async def test_a_sales_draft_is_judged_by_the_sales_limit(
        self, session: AsyncSession, admin: UserModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str | None] = []

        async def ensure(_: AsyncSession, *, own: usage.OwnCap | None = None, **__: Any) -> None:
            seen.append(None if own is None else own.setting)

        monkeypatch.setattr(usage, "ensure_llm_within_cap", ensure)
        stage: Any = SimpleNamespace(own_cap=sales_cap)

        await guarding._within_caps(session, stage, drafts_cap())
        await _save(session, admin, SUGGESTED)
        await guarding._within_caps(session, stage, drafts_cap())

        assert seen == [llm_caps.AGENT, llm_caps.SALES, llm_caps.SALES]

    async def test_who_spends_today_is_counted_like_the_guard(self, session: AsyncSession) -> None:
        await _spend(session, "sales_draft", 300)
        await _spend(session, "agent_draft", 200)
        await _spend(session, "site_judge", 100)
        await _spend(session, "reply_parse", 50)

        spent = await usage.spent_by_part(session, since=datetime.now(UTC) - timedelta(hours=1))

        assert spent == {"sales": 300, "agent": 200, "outreach": 150}


@pytest.mark.usefixtures("env_caps")
class TestChange:
    @pytest.mark.parametrize(
        ("wanted", "said"),
        [
            (Block(7 * M, 20, 20, 30), "одно из чисел списка: 10 млн, 20 млн"),
            (Block(50 * M, 22, 20, 30), "шагом 5"),
            (Block(50 * M, 20, 0, 30), "ноль — не лимит, а запрет"),
            (Block(50 * M, 40, 40, 30), "в сумме 110 % — больше общего"),
        ],
    )
    async def test_refused_in_words_and_nothing_written(
        self, session: AsyncSession, admin: UserModel, wanted: Block, said: str
    ) -> None:
        with pytest.raises(LlmCapError, match=said):
            await llm_caps.set_llm_caps(session, wanted, author=admin)

        assert (await session.scalars(select(LlmCapModel))).all() == []

    @pytest.mark.parametrize("general", [*llm_caps.GENERAL_PRESETS, None])
    async def test_every_general_on_the_list_is_taken(
        self, session: AsyncSession, admin: UserModel, general: int | None
    ) -> None:
        done = await _save(session, admin, Block(general, 20, 20, 30))

        assert done.change.block.general == general
        assert done.change.set_by == admin.email

    async def test_everything_handed_out_is_taken(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        done = await _save(session, admin, Block(50 * M, 40, 30, 30))

        assert done.change.block.free_share == 0

    async def test_newest_first_with_was_and_became(
        self, session: AsyncSession, admin: UserModel
    ) -> None:
        first = await _save(session, admin, SUGGESTED)
        second = await _save(session, admin, Block(None, 20, 20, 30))

        changes = await llm_caps.cap_changes(session)

        assert first.was is None
        assert second.was == SUGGESTED
        assert [change.block.general for change in changes] == [None, 50 * M]

    def test_block_words(self) -> None:
        assert llm_caps.block_words(None) == "как в настройках сервера"
        assert llm_caps.block_words(SUGGESTED) == (
            "общий 50 000 000 токенов в сутки; продажи 20 %, агент переписки 20 %, "
            "аутрич 30 %, свободно 30 %"
        )
        assert llm_caps.block_words(Block(None, 20, 20, 30)).startswith("общий без потолка;")


class TestReset:
    def test_the_reset_is_midnight_utc_and_three_in_moscow(self) -> None:
        assert llm_caps.reset_at(EVENING) == datetime(2026, 10, 30, tzinfo=UTC)
        assert llm_caps.reset_words(EVENING) == "через 5 ч 12 мин (в 03:00 по Москве)"

    @pytest.mark.parametrize(
        ("left", "said"),
        [
            (timedelta(hours=3), "через 3 ч"),
            (timedelta(minutes=42), "через 42 мин"),
            (timedelta(seconds=20), "через 1 мин"),
            (timedelta(hours=23, minutes=59, seconds=1), "через 24 ч"),
        ],
    )
    def test_until_the_reset_in_words(self, left: timedelta, said: str) -> None:
        now = datetime(2026, 10, 30, tzinfo=UTC) - left
        assert llm_caps.until_reset(now) == said

    async def test_the_run_cap_has_no_reset(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 10)

        async def ten(*_: object, **__: object) -> int:
            return 10

        monkeypatch.setattr(usage, "llm_tokens_spent", ten)

        with pytest.raises(LlmCapExceededError) as refused:
            await usage.ensure_llm_within_cap(session, run_id=7, now=EVENING)

        assert str(refused.value).endswith("поднять потолок прогона может администратор")


@pytest.fixture
def quiet_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остатки у провайдеров не спрашиваются: экран расхода — о своей таблице."""

    class _Silent:
        async def limits_and_usage(self) -> None:
            raise OSError("тест: остаток не спрашиваем")

        async def aclose(self) -> None:
            return None

    async def no_serp() -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(settings_routes, "AhrefsClient", _Silent)
    monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)


@pytest.mark.usefixtures("env_caps", "quiet_providers")
class TestScreen:
    async def test_not_saved_the_screen_says_the_env_and_suggests(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, sign_in: SignIn
    ) -> None:
        token = await sign_in(admin.email)
        await _spend(session, "sales_segment_judge", 300)
        await _spend(session, "agent_draft", 200)
        await _spend(session, "site_judge", 100)
        await session.commit()

        card = (await client.get("/api/usage", headers=bearer(token))).json()["llm_caps"]

        assert (card["scheme"], card["general"], card["free_share"], card["saved"]) == (
            "env",
            None,
            None,
            None,
        )
        assert card["suggested"] == _body(SUGGESTED)
        assert card["general_presets"] == list(llm_caps.GENERAL_PRESETS)
        parts = {part["part"]: part for part in card["parts"]}
        assert (parts["sales"]["limit"], parts["sales"]["left"]) == (10 * M, 10 * M - 300)
        assert (parts["agent"]["limit"], parts["outreach"]["limit"]) == (None, None)
        assert (parts["agent"]["spent"], parts["outreach"]["spent"], card["spent"]) == (
            200,
            100,
            600,
        )
        assert card["reset_in"].endswith("(в 03:00 по Москве)")

    async def test_the_admin_saves_and_the_journal_says_was_and_became(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, sign_in: SignIn
    ) -> None:
        token = await sign_in(admin.email)

        saved = await client.post(URL, json=_body(SUGGESTED), headers=bearer(token))

        assert saved.status_code == 200, saved.text
        assert sales_cap().tokens == 10 * M  # память API — сразу
        card = (await client.get("/api/usage", headers=bearer(token))).json()["llm_caps"]
        assert (card["scheme"], card["general"], card["free_share"], card["free"]) == (
            "parts",
            50 * M,
            30,
            15 * M,
        )
        parts = {part["part"]: part for part in card["parts"]}
        assert (parts["outreach"]["share"], parts["outreach"]["limit"]) == (30, 15 * M)
        assert card["changes"][0]["set_by"] == admin.email
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.LLM_CAP_CHANGED)
        )
        assert entry is not None
        words = entry.details["потолки модели"]
        assert words["было"] == "как в настройках сервера"
        assert words["стало"] == llm_caps.block_words(SUGGESTED)

    async def test_no_cap_is_a_journal_line_too(
        self, client: AsyncClient, session: AsyncSession, admin: UserModel, sign_in: SignIn
    ) -> None:
        token = await sign_in(admin.email)

        saved = await client.post(
            URL, json=_body(Block(None, 20, 20, 30)), headers=bearer(token)
        )

        assert saved.status_code == 200, saved.text
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.LLM_CAP_CHANGED)
        )
        assert entry is not None
        assert entry.details["потолки модели"]["стало"].startswith("общий без потолка")

    async def test_an_operator_cannot_change_the_caps(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        operator = await make_user("оператор@team.example.test")
        token = await sign_in(operator.email)

        refused = await client.post(URL, json=_body(SUGGESTED), headers=bearer(token))

        assert refused.status_code == 403
        assert "менять потолки расхода" in refused.json()["detail"]

    async def test_the_sales_limit_needs_the_sales_right(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        admin = await make_user(
            "без-продаж@team.example.test", role=UserRole.ADMIN, permissions={"sales": False}
        )
        token = await sign_in(admin.email)

        card = (await client.get("/api/usage", headers=bearer(token))).json()["llm_caps"]
        refused = await client.post(URL, json=_body(SUGGESTED), headers=bearer(token))

        assert "sales" not in [part["part"] for part in card["parts"]]
        assert refused.status_code == 403

    @pytest.mark.parametrize(
        ("body", "said"),
        [
            (_body(Block(7 * M, 20, 20, 30)), "одно из чисел списка"),
            ({**_body(SUGGESTED), "sales_share": 20.5}, "целые числа"),
        ],
    )
    async def test_a_bad_body_is_refused_in_words(
        self,
        client: AsyncClient,
        admin: UserModel,
        sign_in: SignIn,
        body: dict[str, object],
        said: str,
    ) -> None:
        token = await sign_in(admin.email)

        refused = await client.post(URL, json=body, headers=bearer(token))

        assert refused.status_code == 422
        assert said in refused.text


async def test_the_start_reads_the_block_and_survives_a_dead_base(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Dead:
        async def __aenter__(self) -> None:
            raise OSError("тест: база не отвечает")

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(llm_caps, "SessionFactory", _Dead)

    await llm_caps.load()

    assert "не прочитаны при старте" in caplog.text


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    def down_and_up(sync: Connection) -> tuple[bool, bool]:
        def present() -> bool:
            return bool(sync.scalar(text("SELECT to_regclass('llm_caps') IS NOT NULL")))

        migration = load_migration(MIGRATION)
        with Operations.context(MigrationContext.configure(sync)):
            migration.downgrade()
            down = present()
            migration.upgrade()
        return down, present()

    assert await connection.run_sync(down_and_up) == (False, True)
