"""продажи: повтор сообщения о лиде в Telegram по расписанию

Revision ID: 7737a3ecec66
Revises: 260e2efdd0c6
Create Date: 2026-10-08 16:30:00.000000

Сообщение телемаркетологу или копия в группу, не ушедшие из-за сети, 5xx или 429,
повторяются проходом по расписанию с растущей паузой (`sales/handoff_telegram.py`).
Передаче нужно помнить, сколько раз подряд сообщение не ушло (`telegram_tries`), и не
раньше чего его повторить (`telegram_due_at`: у 429 — не раньше паузы, названной Telegram).

Только добавление колонок: у прежних передач повторять нечего — ноль и пусто.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7737a3ecec66"
down_revision: Union[str, Sequence[str], None] = "260e2efdd0c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Счёт попыток и срок повтора сообщения о лиде."""
    op.add_column(
        "sales_handoffs",
        sa.Column("telegram_tries", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "sales_handoffs",
        sa.Column("telegram_due_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Снять обе колонки."""
    op.drop_column("sales_handoffs", "telegram_due_at")
    op.drop_column("sales_handoffs", "telegram_tries")
