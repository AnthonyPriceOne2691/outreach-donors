"""sent_today считается, а не хранится

Revision ID: 8c1d5e4a72b0
Revises: 2f2479cffc0e
Create Date: 2026-09-21 10:40:00.000000

Колонка `senders.sent_today` была вторым счётчиком к тому, что и так
лежит в письмах, и её никто не обнулял: к концу первых суток она
доходила до дневного капа и оставалась там навсегда, а отправитель
переставал получать письма без единой ошибки. Ровно тот случай, ради
которого в конституции записано «остаток лимита не хранится полем:
два независимых счётчика неизбежно разойдутся».

Сколько ушло сегодня, теперь считается по `messages.sent_at`, и индекс
ниже — под этот запрос.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '8c1d5e4a72b0'
down_revision: Union[str, Sequence[str], None] = '2f2479cffc0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('senders', 'sent_today')
    # «Сколько этот ящик отправил сегодня» — единственный запрос,
    # который идёт на каждое письмо очереди.
    op.create_index(
        'idx_messages_sender_sent_at', 'messages', ['sender_id', 'sent_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_messages_sender_sent_at', table_name='messages')
    op.add_column(
        'senders',
        sa.Column('sent_today', sa.Integer(), nullable=False, server_default='0'),
    )
