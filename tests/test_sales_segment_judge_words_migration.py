"""Ревизия слов, времени и решавшего судьи у сайта сбора (`7b642c7865f2`) — вверх, вниз и снова
вверх на базе теста.

Подъём добавляет пустые колонки `sales_segment_candidates.judge_reason` (text), `judged_at`
(timestamptz) и `judged_by` (код решавшего, как `decided_by`) и строки не переписывает: у сайта,
судимого раньше, слова — в причине, время — в «когда решено», решавший — в «кто решил», колонки
судьи пусты — их заполнит решение человека (`segment_report.decide`). Откат снимает колонки — у
решённых человеком слова судьи снова только в кэше вердиктов, решавшего снова угадывают, как было;
причина, время и «кто решил» сайта целы. Замок — с пределом ожидания в обе стороны (`SET LOCAL
lock_timeout`): строки сайтов пишет проход судьи и заведение лидов, отчёт прогона читает их каждые
три секунды. В ревизии — только литералы: код приложения меняется, а ревизия обязана подниматься
такой, какой её записали.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.models.domain import DomainModel
from backend.features.sales.segment_models import (
    CODE_LENGTH,
    Decider,
    Lane,
    SalesSegmentCandidateModel,
)
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import VERSIONS, load_migration
from tests.test_sales_segment_run import hypothesis, make_run

REVISION = "7b642c7865f2_sales_segment_judge_words.py"
COLUMNS = ("judge_reason", "judged_at", "judged_by")
#: Сайт, судимый до ревизии: слова судьи — в причине, время — в «когда решено».
WORDS = "по тексту не видно, чем занят сайт, — решает человек"
JUDGED = datetime(2026, 10, 20, 16, 2, tzinfo=UTC)
#: Колонки сайта, которые ревизия не трогает, и колонки судьи — строкой выборки.
SITE = "reason, decided_at, decided_by"
JUDGE = "judge_reason, judged_at, judged_by"


def _columns(connection: Connection) -> dict[str, tuple[Any, ...]]:
    """Тип, ширина, умолчание и пустота колонок судьи; колонки нет — нет и строки."""
    rows = connection.execute(
        text(
            "SELECT column_name, data_type, character_maximum_length, column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'sales_segment_candidates' AND column_name = ANY(:names)"
        ),
        {"names": list(COLUMNS)},
    )
    return {row[0]: tuple(row[1:]) for row in rows}


def _file(connection: Connection) -> int:
    """Файл таблицы на диске: переписанная таблица получает новый."""
    return int(
        connection.execute(
            text(
                "SELECT relfilenode FROM pg_class WHERE oid = 'sales_segment_candidates'::regclass"
            )
        ).scalar_one()
    )


def _step(connection: Connection, call: Any) -> str:
    """Шаг ревизии с чистым пределом ожидания — и какой предел шаг поставил себе сам."""
    connection.execute(text("SET LOCAL lock_timeout = 0"))
    call()
    return str(connection.execute(text("SHOW lock_timeout")).scalar_one())


def _row(connection: Connection, site_id: int, columns: str) -> tuple[Any, ...]:
    found = connection.execute(
        text(f"SELECT {columns} FROM sales_segment_candidates WHERE id = :id"),
        {"id": site_id},
    )
    return tuple(found.one())


def _up_down_up(connection: Connection, site_id: int) -> dict[str, Any]:
    migration = load_migration(REVISION)
    seen: dict[str, Any] = {}
    with Operations.context(MigrationContext.configure(connection)):
        seen["start"] = _step(connection, migration.downgrade)  # база теста — цепочкой до головы
        seen["before"] = _row(connection, site_id, SITE)
        file_before = _file(connection)
        seen["up"] = _step(connection, migration.upgrade)
        seen["columns"] = _columns(connection)
        seen["rewritten"] = _file(connection) != file_before
        seen["kept"] = _row(connection, site_id, f"{SITE}, {JUDGE}")
        connection.execute(
            text(
                "UPDATE sales_segment_candidates SET judge_reason = reason, "
                "judged_at = decided_at, judged_by = decided_by WHERE id = :id"
            ),
            {"id": site_id},
        )
        seen["down"] = _step(connection, migration.downgrade)
        seen["gone"] = _columns(connection)
        seen["after"] = _row(connection, site_id, SITE)
        migration.upgrade()
        seen["again"] = _row(connection, site_id, JUDGE)
    return seen


async def test_upgrade_downgrade_upgrade_adds_empty_judge_columns_and_rewrites_nothing(
    session: AsyncSession,
) -> None:
    run = await make_run(session, await hypothesis(session))
    domain = DomainModel(host="cerrajero-24h.example.test")
    session.add(domain)
    await session.flush()
    site = SalesSegmentCandidateModel(
        run_id=run.id,
        hypothesis_id=run.hypothesis_id,
        domain_id=domain.id,
        lane=Lane.REVIEW,
        verdict=Lane.REVIEW,
        reason=WORDS,
        decided_by=Decider.MODEL,
        decided_at=JUDGED,
        model="gpt-5-mini",
    )
    session.add(site)
    await session.flush()
    connection = await session.connection()

    seen = await connection.run_sync(_up_down_up, site.id)

    assert (seen["start"], seen["up"], seen["down"]) == ("5s", "5s", "5s")
    assert seen["before"] == (WORDS, JUDGED, Decider.MODEL)
    assert seen["columns"] == {
        "judge_reason": ("text", None, None, "YES"),
        "judged_at": ("timestamp with time zone", None, None, "YES"),
        "judged_by": ("character varying", CODE_LENGTH, None, "YES"),
    }
    assert seen["rewritten"] is False
    assert seen["kept"] == (WORDS, JUDGED, Decider.MODEL, None, None, None)
    assert (seen["gone"], seen["after"]) == ({}, (WORDS, JUDGED, Decider.MODEL))
    assert seen["again"] == (None, None, None)
    assert set(COLUMNS) <= set(SalesSegmentCandidateModel.__table__.columns.keys())


def test_the_revision_follows_the_head_and_holds_only_literals() -> None:
    """Ревизия не берёт код приложения: имена таблицы, колонок и предел — литералами в файле."""
    migration = load_migration(REVISION)
    tree = ast.parse((VERSIONS / REVISION).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }

    revisions = ("7b642c7865f2", "abcc1e4edf4a")  # pragma: allowlist secret — номера ревизий
    assert (migration.revision, migration.down_revision) == revisions
    assert imported == {"typing", "sqlalchemy", "alembic"}
