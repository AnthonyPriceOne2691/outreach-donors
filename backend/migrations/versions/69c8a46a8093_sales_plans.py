"""план работ продаж: план, гипотезы плана, реестр ключей; страница и план у прогона; вид сайта

Revision ID: 69c8a46a8093
Revises: c050e74c96dd
Create Date: 2026-11-16 16:09:09.329717

Доработка «План работ» (14.11.2026, решения владельца 16.11): одна книга Excel задаёт работу на неделю
или месяц, лист — гипотеза продаж, а конвейер собирает гипотезы по очереди с нарастающей глубиной
выдачи (`sales/plan_models.py`):

- `sales_plans` — план: режим, общая цель, потолок плана (обязателен), смета и её отпечаток;
- `sales_plan_hypotheses` — лист: своя гипотеза (уникальна), порядок листа, рынки, язык писем, цель,
  предельная глубина, потолок гипотезы (обязателен), этап и числа конвейера;
- `sales_plan_keywords` — реестр ключей: ключ листа на рынке и докуда куплена выдача (`pages`);
- `sales_segment_runs.from_page` (с какой страницы прогон покупает; у прежних — 1) и
  `sales_segment_runs.plan_hypothesis_id` (чей прогон; у прежних — пусто);
- `sales_hypotheses.site_kinds` — вид сайта (у прежних — пусто: как сейчас).

Таблицы новые и пустые. **Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN берёт
ACCESS EXCLUSIVE на прогоны и гипотезы, и за долгой транзакцией прохода сбора выкатка встала бы, а за
ней — все запросы к ним; с пределом она честно падает за 5 с, и её повторяют. Умолчания колонок —
постоянные: таблицы не переписываются. Откат — в обратном порядке; данных плана после него нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "69c8a46a8093"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c050e74c96dd"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
RUNS = "sales_segment_runs"
RUN_PLAN_INDEX = "idx_sales_segment_runs_plan_hypothesis"


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def _moments(*names: str) -> list[sa.Column]:
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=True) for name in names]


def _plans() -> None:
    op.create_table(
        "sales_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("target_default", sa.Integer(), nullable=True),
        sa.Column("cap_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("digest", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        *_moments("finished_at"),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
    )


def _plan_hypotheses() -> None:
    op.create_table(
        "sales_plan_hypotheses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("sheet", sa.String(length=255), nullable=False),
        sa.Column("markets", postgresql.ARRAY(sa.String(length=8)), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("max_pages", sa.SmallInteger(), nullable=False),
        sa.Column("cap_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("wave_page", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column(
            "problems",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("spent_usd", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("reserved_usd", sa.Numeric(12, 4), server_default="0", nullable=False),
        *_moments("counted_at", "started_at", "finished_at"),
        *_stamps(),
        sa.ForeignKeyConstraint(["plan_id"], ["sales_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hypothesis_id"),
        sa.UniqueConstraint("plan_id", "position", name="uq_sales_plan_hypotheses_position"),
    )


def _keywords() -> None:
    op.create_table(
        "sales_plan_keywords",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("plan_hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("keyword_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("pages", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_hypothesis_id"], ["sales_plan_hypotheses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["sales_segment_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_hypothesis_id", "market", "keyword_key", name="uq_sales_plan_keywords_key"
        ),
    )
    op.create_index(
        "idx_sales_plan_keywords_pages",
        "sales_plan_keywords",
        ["plan_hypothesis_id", "market", "pages"],
    )
    op.create_index("idx_sales_plan_keywords_run", "sales_plan_keywords", ["run_id"])


def upgrade() -> None:
    """Таблицы плана, колонки прогона и поле «вид сайта» у гипотезы."""
    op.execute(LOCK_TIMEOUT)
    _plans()
    _plan_hypotheses()
    _keywords()
    op.add_column(
        RUNS, sa.Column("from_page", sa.SmallInteger(), server_default="1", nullable=False)
    )
    # Ключ без имени — имя по умолчанию Postgres, как у модели; откат колонки снимает и его.
    op.add_column(
        RUNS,
        sa.Column(
            "plan_hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("sales_plan_hypotheses.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(RUN_PLAN_INDEX, RUNS, ["plan_hypothesis_id"])
    op.add_column(
        "sales_hypotheses",
        sa.Column(
            "site_kinds",
            postgresql.ARRAY(sa.String(length=16)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Снять поле, колонки прогона и таблицы плана: прогоны и гипотезы остаются как были до плана."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("sales_hypotheses", "site_kinds")
    op.drop_index(RUN_PLAN_INDEX, table_name=RUNS)
    op.drop_column(RUNS, "plan_hypothesis_id")
    op.drop_column(RUNS, "from_page")
    op.drop_index("idx_sales_plan_keywords_run", table_name="sales_plan_keywords")
    op.drop_index("idx_sales_plan_keywords_pages", table_name="sales_plan_keywords")
    op.drop_table("sales_plan_keywords")
    op.drop_table("sales_plan_hypotheses")
    op.drop_table("sales_plans")
