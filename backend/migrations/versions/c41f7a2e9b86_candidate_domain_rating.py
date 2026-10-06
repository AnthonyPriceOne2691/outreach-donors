"""кандидаты в рекламодатели: DR домена — «DR > 80 — не пишем»

Revision ID: c41f7a2e9b86
Revises: b7c3e91d0a52
Create Date: 2026-10-06 17:00:00.000000

Строка требования «Кому не пишем … домены DR > 80» до этой ревизии
не выполнялась нигде: DR — метрика провайдера, списком её не задать.
DR берётся пакетом при пересчёте кандидатов и хранится у кандидата,
чтобы следующий пересчёт его не покупал заново.

`dr_checked_at` пусто — не спрашивали; заполнено при пустом `dr` —
спросили, а провайдер домена не знает.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c41f7a2e9b86"
down_revision: Union[str, Sequence[str], None] = "b7c3e91d0a52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("advertiser_candidates", sa.Column("dr", sa.Integer(), nullable=True))
    op.add_column(
        "advertiser_candidates",
        sa.Column("dr_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("advertiser_candidates", "dr_checked_at")
    op.drop_column("advertiser_candidates", "dr")
