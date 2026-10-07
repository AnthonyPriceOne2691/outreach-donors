"""продажи: журнал сообщений о черновиках агента в группу продаж

Revision ID: d64e2cd71614
Revises: 1cbf4c6b63f0
Create Date: 2026-10-07 15:31:00.000000

О каждом черновике агента продаж, который ждёт человека, бот продаж пишет в
группу продаж (срез 3.5). Строка журнала — одна версия черновика (`written_at`):
что ушло, доставлено ли, а если нет — почему. Исход — строкой (`sent`,
`undelivered`), а не типом базы: новый исход не требует миграции.

Черновик удалён (чистка пробного ответа уносит его каскадом) — его строки уходят
с ним. Миграция только создаёт: строк не заводит.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d64e2cd71614"
down_revision: Union[str, Sequence[str], None] = "1cbf4c6b63f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _when() -> list[sa.Column]:
    """Когда строка заведена и правлена — серверное время, как у примеси моделей."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    """Таблица журнала: версия черновика, исход, текст сообщения, причина недоставки."""
    op.create_table(
        "sales_draft_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *_when(),
        sa.ForeignKeyConstraint(["draft_id"], ["agent_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "written_at", name="uq_sales_draft_notices_version"),
    )


def downgrade() -> None:
    """Снять таблицу."""
    op.drop_table("sales_draft_notices")
