"""лид продаж из ответа «пишите другому» — ссылка на исходный диалог

Revision ID: 739817077a57
Revises: 75242c2ed7ba
Create Date: 2026-10-07 01:00:00.000000

Ответ «это не ко мне, пишите …» заводит нового лида той же компании
(`features/sales/referral.py`, источник `referral` — значение типа уже есть).
Откуда лид взялся, хранит ссылка на диалог, в котором его назвали. Чистка
липовой переписки лида не уносит: ссылка обнуляется (`SET NULL`).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "739817077a57"
down_revision: Union[str, Sequence[str], None] = "75242c2ed7ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sales_leads", sa.Column("referred_from_thread_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        None,
        "sales_leads",
        "threads",
        ["referred_from_thread_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_sales_leads_referred_from_thread",
        "sales_leads",
        ["referred_from_thread_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_sales_leads_referred_from_thread", table_name="sales_leads")
    op.drop_column("sales_leads", "referred_from_thread_id")
