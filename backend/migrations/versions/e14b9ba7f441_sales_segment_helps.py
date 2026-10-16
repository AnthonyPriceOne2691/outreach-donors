"""ответы помощника описания сегмента — кэш по черновику, версии промпта и модели

Revision ID: e14b9ba7f441
Revises: f0ec1a11f480
Create Date: 2026-10-16 15:00:00.000000

Решение владельца 16.10.2026: помощник описания сегмента — обязательно, от описания зависит
конечный результат сбора. Помощник переписывает черновик описания в описание для судьи
сегмента (`sales/segment_helper.py`); строка — его ответ на один черновик: тот же черновик под
той же версией промпта и моделью второй раз не оплачивается. Таблица новая и пустая — до неё
помощника не было.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e14b9ba7f441"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f0ec1a11f480"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица ответов помощника."""
    op.create_table(
        "sales_segment_helps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("draft_digest", sa.String(length=16), nullable=False),
        sa.Column("draft", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        *(
            sa.Column(
                name,
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            )
            for name in ("unclear", "problems")
        ),
        sa.Column("tokens", sa.Integer(), server_default="0", nullable=False),
        *(
            sa.Column(
                name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
            )
            for name in ("created_at", "updated_at")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prompt_version", "model", "draft_digest", name="uq_sales_segment_helps_key"
        ),
    )


def downgrade() -> None:
    """Снять таблицу: кэш помощника — только кэш, следующий вызов спросит модель снова."""
    op.drop_table("sales_segment_helps")
