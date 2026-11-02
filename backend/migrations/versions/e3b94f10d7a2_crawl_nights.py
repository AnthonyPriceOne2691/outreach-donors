"""ночной обход: строка круга на дату

Revision ID: e3b94f10d7a2
Revises: 7a1e6d40c2f9
Create Date: 2026-11-02 17:00:00.000000

Ночной круг (`crawl/night.py`) начинается один раз за ночь: уникальная дата держит это при
рестарте и двух процессах, а расчёт и числа остаются следом — что считали и что вышло.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3b94f10d7a2"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "7a1e6d40c2f9"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "crawl_nights"


def upgrade() -> None:
    """Таблица кругов."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("night", sa.Date(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issue_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("given", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    """Без таблицы круг не начнётся: выключен и сам обход."""
    op.drop_table(TABLE)
