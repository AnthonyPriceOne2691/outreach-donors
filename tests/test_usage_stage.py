"""Этап у строки расхода и разбивка по этапам (требование ТЗ).

До 12.10.2026 у журнала расхода этапа не было: DR кандидатов Этапа 2 писался той же
операцией `batch_metrics`, что и прогон Этапа 1, и на экране «Расход» обе траты были
одной строкой. Проверяется, откуда строка берёт этап (операция или вызов), что разбивка
не складывает разные валюты, что этап продаж не виден без права, и что ревизия
размечает только то, что можно разметить без догадки.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.settings import routes as settings_routes
from backend.config import crawl as crawl_cfg
from backend.features.ahrefs.units import UnitsCost
from backend.features.core import usage
from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import SpendingRepository, crawl_spent_this_month
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import columns_down_and_up, load_migration

MIGRATION = "444de76032e8_usage_stage.py"
NO_SALES = frozenset({Stage.DONORS, Stage.ADVERTISERS})


class TestStageOfRecord:
    @pytest.mark.parametrize(
        ("operation", "stage"),
        [
            ("by_country", Stage.DONORS),
            ("serp_search", Stage.DONORS),
            ("site_judge", Stage.DONORS),
            ("keywords", Stage.DONORS),
            ("crawl_proxy", Stage.ADVERTISERS),
            ("crawl_render", Stage.ADVERTISERS),
            ("sales_draft", Stage.SALES),
            ("sales_verify", Stage.SALES),
        ],
    )
    async def test_operation_with_one_stage_names_it_itself(
        self, session: AsyncSession, operation: str, stage: Stage
    ) -> None:
        row = usage.record(session, operation=operation, units=1)

        assert row.stage is stage

    @pytest.mark.parametrize(
        "operation", ["batch_metrics", "contacts_search", "letter_rewrite", "letter_send"]
    )
    async def test_shared_operation_without_a_stage_stays_without_one(
        self, session: AsyncSession, operation: str
    ) -> None:
        """Метрики доменов делят прогон Этапа 1 и DR кандидатов Этапа 2: догадка «доноры»
        разошлась бы со счётом молча. Отправка письма — общая на все этапы нарочно."""
        row = usage.record(session, operation=operation, units=1)

        assert row.stage is None

    async def test_the_call_names_the_stage_of_a_shared_operation(
        self, session: AsyncSession
    ) -> None:
        row = usage.record(session, operation="batch_metrics", units=50, stage=Stage.ADVERTISERS)

        assert row.stage is Stage.ADVERTISERS

    async def test_run_records_its_metrics_with_its_stage(self, session: AsyncSession) -> None:
        """Прогон пишет метрики своим этапом (`pipeline.flush_usage`): без него строка
        метрик осталась бы без этапа."""
        cost = UnitsCost(actual=200, estimated=200, per_row=2)

        row = await RunRepository(session).record_usage(
            run_id=None, operation="batch_metrics", cost=cost, stage=Stage.DONORS
        )

        assert (row.stage, row.units) == (Stage.DONORS, 200)


def _row(
    operation: str, *, units: int = 0, amount: str | None = None, stage: Stage | None = None
) -> UsageRecordModel:
    """Строка журнала тем же правилом этапа, что у `usage.record`."""
    return UsageRecordModel(
        system=usage.SYSTEM,
        provider=usage.OPERATION_PROVIDERS[operation],
        operation=operation,
        units=units,
        amount_usd=None if amount is None else Decimal(amount),
        stage=stage if stage is not None else usage.OPERATION_STAGES.get(operation),
    )


class TestSpendingByStage:
    async def test_one_operation_of_two_stages_is_two_lines(self, session: AsyncSession) -> None:
        """DR кандидатов и метрики прогона — обе `batch_metrics`, а строки разные."""
        session.add_all(
            [
                _row("batch_metrics", units=900, stage=Stage.DONORS),
                _row("batch_metrics", units=100, stage=Stage.DONORS),
                _row("batch_metrics", units=50, stage=Stage.ADVERTISERS),
            ]
        )
        await session.flush()

        spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE)

        lines = {(a.operation, a.stage): (a.units, a.calls) for a in spending.articles}
        assert lines == {
            ("batch_metrics", Stage.DONORS): (1000, 2),
            ("batch_metrics", Stage.ADVERTISERS): (50, 1),
        }
        assert spending.units_by_provider == {UsageProvider.AHREFS: 1050}

    async def test_stages_keep_currencies_apart_and_unstaged_comes_last(
        self, session: AsyncSession
    ) -> None:
        session.add_all(
            [
                _row("letter_send", units=4),
                _row("batch_metrics", units=50, stage=Stage.ADVERTISERS),
                _row("crawl_proxy", units=2_000_000, amount="0.0100"),
                _row("crawl_render", units=3, amount="0.0480"),
                _row("site_judge", units=1200, stage=Stage.DONORS),
            ]
        )
        await session.flush()

        spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE)

        stages = {spent.stage: spent for spent in spending.by_stage}
        assert [spent.stage for spent in spending.by_stage] == [
            Stage.DONORS,
            Stage.ADVERTISERS,
            None,
        ]
        second = stages[Stage.ADVERTISERS]
        assert second.units_by_provider == {
            UsageProvider.AHREFS: 50,
            UsageProvider.PROXY: 2_000_000,
            UsageProvider.RENDER: 3,
        }
        assert second.amount_usd == Decimal("0.0580")
        assert second.calls == 3
        assert stages[None].units_by_provider == {UsageProvider.EMAIL: 4}

    async def test_a_line_called_sales_is_hidden_without_the_right(
        self, session: AsyncSession
    ) -> None:
        """Строка, которую вызов назвал тратой продаж, без права не видна, даже если
        операция общая; строка без этапа — видна: она продажам не принадлежит."""
        session.add_all(
            [
                _row("letter_send", units=2, stage=Stage.SALES),
                _row("letter_send", units=5),
                _row("sales_draft", units=40),
            ]
        )
        await session.flush()

        hidden = await SpendingRepository(session).since_month_start(stages=NO_SALES)
        shown = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE)

        assert [(a.operation, a.stage, a.units) for a in hidden.articles] == [
            ("letter_send", None, 5)
        ]
        assert [spent.stage for spent in hidden.by_stage] == [None]
        assert {spent.stage for spent in shown.by_stage} == {Stage.SALES, None}


class TestCrawlSpent:
    async def test_counts_money_of_paid_crawl_this_month_only(self, session: AsyncSession) -> None:
        old = _row("crawl_proxy", units=10, amount="5.0000")
        session.add_all(
            [
                _row("crawl_proxy", units=1_000_000, amount="0.0050"),
                _row("crawl_render", units=1, amount="0.0160"),
                _row("crawl_proxy", units=1_000_000),  # цена не задана — денег нет
                _row("serp_search", amount="0.6000"),
                old,
            ]
        )
        await session.flush()
        old.created_at = datetime.now(UTC).replace(day=1) - timedelta(days=3)
        await session.flush()

        assert await crawl_spent_this_month(session) == Decimal("0.0210")

    async def test_the_screen_counts_it_by_the_same_visibility_rule(
        self, session: AsyncSession
    ) -> None:
        """Карточка `/api/usage` — тем же правилом видимости, что статьи (`_seen`): строка,
        которую вызов назвал тратой продаж, без права «Продажи» не видна и в ней."""
        session.add_all(
            [
                _row("crawl_render", units=1, amount="0.0160"),
                _row("crawl_render", units=1, amount="0.5000", stage=Stage.SALES),
            ]
        )
        await session.flush()

        assert await crawl_spent_this_month(session, stages=NO_SALES) == Decimal("0.0160")
        assert await crawl_spent_this_month(session) == Decimal("0.5160")


def _migrate(connection: Connection) -> dict[str, str | None]:
    """Ревизию вниз, строки старого вида, ревизию вверх — что легло в этап."""
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        for operation, run_marker in [
            ("serp_search", None),
            ("site_judge", None),
            ("dr_screen", None),
            ("batch_metrics", None),
            ("letter_send", None),
            ("contacts_search", None),
            ("sales_draft", None),
            ("sales_verify", None),
        ]:
            connection.execute(
                text(
                    "INSERT INTO usage_records (system, provider, operation, units, run_id, "
                    "created_at, updated_at) VALUES ('outreach-donors', 'llm', :operation, 1, "
                    ":run, now(), now())"
                ),
                {"operation": operation, "run": run_marker},
            )
        migration.upgrade()
    rows = connection.execute(text("SELECT operation, stage::text FROM usage_records"))
    return dict(rows.tuples().all())


async def test_revision_marks_only_what_has_one_stage(session: AsyncSession) -> None:
    """Выдача, судья и прежние имена — Этап 1; операции продаж — продажи. Метрики доменов,
    поиск адресов и отправка — без этапа: их делят этапы, а догадка разошлась бы со счётом."""
    connection = await session.connection()

    stages = await connection.run_sync(_migrate)

    assert stages == {
        "serp_search": "donors",
        "site_judge": "donors",
        "dr_screen": "donors",
        "batch_metrics": None,
        "letter_send": None,
        "contacts_search": None,
        "sales_draft": "sales",
        "sales_verify": "sales",
    }


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(columns_down_and_up, MIGRATION, "usage_records", {"stage"})

    assert (down, up) == (set(), {"stage"})


@pytest.fixture
def quiet_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остатки у провайдеров не спрашиваются: тест — о своей таблице."""

    class _Silent:
        async def limits_and_usage(self) -> None:
            raise OSError("тест: остаток не спрашиваем")

        async def aclose(self) -> None:
            return None

    async def no_serp() -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(settings_routes, "AhrefsClient", _Silent)
    monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)


@pytest.mark.usefixtures("quiet_providers")
@pytest.mark.parametrize(("cap", "expected"), [(None, None), (120.0, "120.00")])
async def test_usage_answers_with_stages_and_the_stage_two_cap(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., Awaitable[UserModel]],
    sign_in: Callable[..., Awaitable[str]],
    monkeypatch: pytest.MonkeyPatch,
    cap: float | None,
    expected: str | None,
) -> None:
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", cap)
    session.add_all(
        [
            _row("batch_metrics", units=900, stage=Stage.DONORS),
            _row("batch_metrics", units=50, stage=Stage.ADVERTISERS),
            _row("crawl_render", units=2, amount="0.0320"),
        ]
    )
    await session.commit()
    await make_user("viewer@team.example.test", permissions={"sales": False})
    headers = bearer(await sign_in("viewer@team.example.test"))

    body = (await client.get("/api/usage", headers=headers)).json()

    assert [card["stage"] for card in body["stages"]] == ["donors", "advertisers"]
    second = body["stages"][1]
    assert second["units_by_provider"] == {"ahrefs": 50, "render": 2}
    assert Decimal(second["amount_usd"]) == Decimal("0.032")
    assert {(line["operation"], line["stage"]) for line in body["articles"]} == {
        ("batch_metrics", "donors"),
        ("batch_metrics", "advertisers"),
        ("crawl_render", "advertisers"),
    }
    assert body["crawl"]["cap_usd"] == expected
    assert Decimal(body["crawl"]["spent_usd"]) == Decimal("0.032")


def test_every_paid_crawl_operation_belongs_to_stage_two() -> None:
    """Свой потолок Этапа 2 считает операции обхода — они обязаны быть его этапом
    и своими провайдерами, иначе потолок и экран считали бы разное."""
    for operation in usage.CRAWL_OPERATIONS:
        assert usage.OPERATION_STAGES[operation] is Stage.ADVERTISERS
        assert usage.OPERATION_PROVIDERS[operation] in (UsageProvider.PROXY, UsageProvider.RENDER)


async def test_stored_stage_reads_back_as_the_enum(session: AsyncSession) -> None:
    usage.record(session, operation="crawl_proxy", units=1234, amount_usd=Decimal("0.01"))
    await session.flush()

    stored = (await session.execute(select(UsageRecordModel))).scalar_one()

    assert (stored.stage, stored.provider) == (Stage.ADVERTISERS, UsageProvider.PROXY)
