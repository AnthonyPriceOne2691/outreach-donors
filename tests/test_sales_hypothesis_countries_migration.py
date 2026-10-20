"""Ревизия поля гипотезы «страны компаний» и фактов судьи v4 (`c7d2e9a14b38`) — вниз и вверх на базе
теста.

Откат снимает поле гипотезы и факты из кэша вердиктов: прежний судья их не знает, а кэш v4 без фактов
не отдаётся — следующий проход спросит модель снова. Подъём возвращает их такими, как у модели: у
прежних гипотез — «на проверку» и пустой список «не пишем» (решение владельца 20.10: так у каждой
гипотезы, пока поле не поправили), у прежних вердиктов фактов нет.
"""

from __future__ import annotations

from typing import Any

from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_models import SalesSegmentVerdictModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

REVISION = "c7d2e9a14b38_sales_hypothesis_countries.py"
FIELD = {"other_countries", "excluded_countries"}
FACTS = {"facts"}


def _columns(connection: Connection, table: str, names: set[str]) -> list[tuple[Any, ...]]:
    rows = connection.execute(
        text(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = ANY(:names) ORDER BY column_name"
        ),
        {"table": table, "names": sorted(names)},
    ).all()
    return [tuple(row) for row in rows]


def _down_and_up(connection: Connection) -> dict[str, Any]:
    connection.execute(text("INSERT INTO sales_hypotheses (name) VALUES ('прежняя гипотеза')"))
    field = columns_down_and_up(connection, REVISION, SalesHypothesisModel.__tablename__, FIELD)
    kept = connection.execute(
        text(
            "SELECT other_countries, excluded_countries FROM sales_hypotheses "
            "WHERE name = 'прежняя гипотеза'"
        )
    ).one()
    facts = columns_down_and_up(connection, REVISION, SalesSegmentVerdictModel.__tablename__, FACTS)
    return {
        "field": field,
        "facts": facts,
        "kept": tuple(kept),
        "field_columns": _columns(connection, "sales_hypotheses", FIELD),
        "facts_columns": _columns(connection, "sales_segment_verdicts", FACTS),
    }


async def test_rollback_drops_the_field_and_the_facts_and_upgrade_brings_them_back(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    found = await connection.run_sync(_down_and_up)

    assert found["field"] == (set(), FIELD)
    assert found["facts"] == (set(), FACTS)
    # Прежняя гипотеза после подъёма — «на проверку» и пустой список «не пишем».
    assert found["kept"] == ("review", [])
    assert found["field_columns"] == [
        ("excluded_countries", "'{}'::character varying[]", "NO"),
        ("other_countries", "'review'::character varying", "NO"),
    ]
    assert found["facts_columns"] == [("facts", None, "YES")]
    assert set(SalesHypothesisModel.__table__.columns.keys()) >= FIELD
    assert set(SalesSegmentVerdictModel.__table__.columns.keys()) >= FACTS
