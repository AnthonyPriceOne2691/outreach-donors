"""журнал здоровья ящика — мягкие отказы, жалобы, снижение лимита, пауза

Revision ID: 3f9663d69a56
Revises: e054d221b2df
Create Date: 2026-10-07 19:00:00.000000

Только новая таблица: строка на мягкий сигнал ящика этапа, чья политика их слушает
(у продаж). Снижение лимита на сутки не хранится числом — считается по строкам.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3f9663d69a56"
down_revision: Union[str, Sequence[str], None] = "e054d221b2df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Журнал здоровья ящиков."""
    op.create_table(
        "sender_health",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sender_id", sa.Integer(), sa.ForeignKey("senders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(length=256), nullable=True),
    )
    op.create_index("idx_sender_health_sender_at", "sender_health", ["sender_id", "at"])


def downgrade() -> None:
    """Снять журнал: мягкие сигналы перестают снижать лимит, паузы остаются у ящиков."""
    op.drop_index("idx_sender_health_sender_at", table_name="sender_health")
    op.drop_table("sender_health")
