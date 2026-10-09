"""продажи: повтор сообщения о черновике агента в Telegram по расписанию

Revision ID: d2afdd4a00d0
Revises: 7737a3ecec66
Create Date: 2026-10-09 13:00:00.000000

Сообщение о черновике агента продаж в группу продаж, не ушедшее из-за сети, 5xx или 429,
повторяется проходом по расписанию той же серией, что сообщение о лиде
(`sales/telegram_series.py`, `sales/agent/notify_retry.py`). Строке журнала отправки нужно
помнить, сколько раз подряд сообщение о версии черновика не ушло (`tries`), и не раньше чего
его повторить (`due_at`: у 429 — не раньше паузы, названной Telegram).

Только добавление колонок: у прежних строк повторять нечего — ноль и пусто.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2afdd4a00d0"
down_revision: Union[str, Sequence[str], None] = "7737a3ecec66"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Счёт попыток и срок повтора сообщения о черновике."""
    op.add_column(
        "sales_draft_notices",
        sa.Column("tries", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "sales_draft_notices",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Снять обе колонки."""
    op.drop_column("sales_draft_notices", "due_at")
    op.drop_column("sales_draft_notices", "tries")
