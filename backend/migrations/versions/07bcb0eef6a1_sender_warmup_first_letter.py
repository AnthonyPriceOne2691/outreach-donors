"""ящик рассылки: первое письмо разгона — ступень считается от него, а не от старта

Revision ID: 07bcb0eef6a1
Revises: b2716edd5d46
Create Date: 2026-10-13 12:00:00.000000

Ступень разгона считалась от старта (`senders.warmup_started_at`): его ставят заведение
ящика и «Включить заново», а писать ящик начинает позже — ящик, заведённый за неделю до
первого письма, начинал сразу с верхней ступени. Теперь ступень считается от первого
письма ящика после старта (`warmup_first_sent_at`, ставит запись «ушло»), а пока его
нет — первая ступень (ревью стыков с продажами, 13.10.2026).

Отметка заполняется по уже ушедшим письмам: у ящика, который после старта писал, —
время его первого такого письма, и ступень считается от него; у ящика, который не писал,
и у ящика без старта (разгон кончен) — пусто. Индекс по ящику и времени ухода у писем
уже есть (`idx_messages_sender_sent_at`), ящиков — десятки: без пакетов.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "07bcb0eef6a1"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b2716edd5d46"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Первое письмо ящика, ушедшее не раньше старта разгона, — по уже ушедшим письмам.
_FIRST_LETTER = sa.text(
    """
    UPDATE senders AS s
    SET warmup_first_sent_at = (
        SELECT min(m.sent_at)
        FROM messages AS m
        WHERE m.sender_id = s.id AND m.sent_at >= s.warmup_started_at
    )
    WHERE s.warmup_started_at IS NOT NULL
    """
)


def upgrade() -> None:
    """Колонка отметки и её значение по ушедшим письмам."""
    op.add_column(
        "senders",
        sa.Column("warmup_first_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(_FIRST_LETTER)


def downgrade() -> None:
    """Снять колонку: ступень снова считается от старта разгона."""
    op.drop_column("senders", "warmup_first_sent_at")
