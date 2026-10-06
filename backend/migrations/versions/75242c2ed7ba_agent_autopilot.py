"""агент переписки: режим автопилота и предел его ответов в переписке

Revision ID: 75242c2ed7ba
Revises: 3924977db911
Create Date: 2026-10-04 23:40:00.000000

Решение владельца 04.10.2026: два режима агента, по умолчанию черновики;
автопилот — переключатель с границами. У настроек этапа появляются режим и
предел ответов автопилота в одной переписке. Письмо, отправленное
автопилотом, помнит сам черновик (`sent_message_id`, решил `autopilot`).

Только добавление: у прежних версий настроек режим «черновики» и предел 2 —
умолчанием базы.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "75242c2ed7ba"
down_revision: Union[str, Sequence[str], None] = "3924977db911"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Режим и предел ответов автопилота у настроек этапа."""
    op.add_column(
        "agent_settings",
        sa.Column("mode", sa.String(length=16), server_default="drafts", nullable=False),
    )
    op.add_column(
        "agent_settings",
        sa.Column("max_turns", sa.SmallInteger(), server_default="2", nullable=False),
    )


def downgrade() -> None:
    """Снять колонки — в обратном порядке."""
    op.drop_column("agent_settings", "max_turns")
    op.drop_column("agent_settings", "mode")
