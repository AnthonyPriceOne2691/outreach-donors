"""Ревизия плана работ (`69c8a46a8093`) — вниз и вверх на базе теста.

Откат снимает таблицы плана, колонки прогона и поле «вид сайта» у гипотезы: прогоны и гипотезы
остаются такими, какими были до плана. Подъём возвращает всё как у моделей: у прежнего прогона —
страница 1 и плана нет, у прежней гипотезы вид сайта пуст (как сейчас), ключи и индексы реестра — на
месте.
"""

from __future__ import annotations

from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_models import (
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
    SalesPlanModel,
)
from backend.features.sales.segment_models import SalesSegmentRunModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration

REVISION = "69c8a46a8093_sales_plans.py"
MODELS = (SalesPlanModel, SalesPlanHypothesisModel, SalesPlanKeywordModel)
TABLES = {model.__tablename__ for model in MODELS}
RUN_COLUMNS = {"from_page", "plan_hypothesis_id"}
KINDS = {"site_kinds"}


def _columns(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
        {"table": table},
    )
    return set(rows.scalars())


def _indexes(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = ANY(:tables)"),
        {"tables": [*sorted(TABLES), SalesSegmentRunModel.__tablename__]},
    )
    return {name for name in rows.scalars() if "plan" in name}


def _shape(connection: Connection) -> dict[str, Any]:
    present = {table for table in TABLES if _columns(connection, table)}
    return {
        "tables": present,
        "run": _columns(connection, SalesSegmentRunModel.__tablename__) & RUN_COLUMNS,
        "hypothesis": _columns(connection, SalesHypothesisModel.__tablename__) & KINDS,
        "indexes": _indexes(connection),
    }


def _old_rows(connection: Connection) -> int:
    """Гипотеза и прогон, заведённые до ревизии."""
    hypothesis = connection.execute(
        text("INSERT INTO sales_hypotheses (name) VALUES ('прежняя гипотеза') RETURNING id")
    ).scalar_one()
    return int(
        connection.execute(
            text(
                "INSERT INTO sales_segment_runs "
                "(hypothesis_id, keywords, country, language, depth_pages, mode, estimate, stage) "
                "VALUES (:id, ARRAY['ключ'], 'kz', 'ru', 1, 'shadow', '{}'::jsonb, 'done') "
                "RETURNING id"
            ),
            {"id": hypothesis},
        ).scalar_one()
    )


def _down_and_up(connection: Connection) -> dict[str, Any]:
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _shape(connection)
        run_id = _old_rows(connection)
        migration.upgrade()
    kept = connection.execute(
        text(
            "SELECT r.from_page, r.plan_hypothesis_id, h.site_kinds FROM sales_segment_runs r "
            "JOIN sales_hypotheses h ON h.id = r.hypothesis_id WHERE r.id = :id"
        ),
        {"id": run_id},
    ).one()
    return {"down": down, "up": _shape(connection), "kept": tuple(kept)} | {
        table: _columns(connection, table) for table in TABLES
    }


async def test_rollback_drops_the_plan_and_upgrade_brings_it_back_as_the_models(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    found = await connection.run_sync(_down_and_up)

    assert found["down"] == {"tables": set(), "run": set(), "hypothesis": set(), "indexes": set()}
    assert found["up"] == {
        "tables": TABLES,
        "run": RUN_COLUMNS,
        "hypothesis": KINDS,
        "indexes": {
            "idx_sales_plan_keywords_pages",
            "idx_sales_plan_keywords_run",
            "idx_sales_segment_runs_plan_hypothesis",
            "sales_plans_pkey",
            "sales_plan_hypotheses_pkey",
            "sales_plan_hypotheses_hypothesis_id_key",
            "sales_plan_keywords_pkey",
            "uq_sales_plan_hypotheses_position",
            "uq_sales_plan_keywords_key",
        },
    }
    # Прежний прогон — с первой страницы и без плана; прежняя гипотеза — вид сайта пуст.
    assert found["kept"] == (1, None, [])
    for model in MODELS:
        assert found[model.__tablename__] == set(model.__table__.columns.keys())
