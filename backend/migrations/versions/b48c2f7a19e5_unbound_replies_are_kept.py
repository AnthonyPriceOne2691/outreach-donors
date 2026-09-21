"""непривязанный ответ сохраняется, а не выбрасывается

Revision ID: b48c2f7a19e5
Revises: a71e3d9c05b2
Create Date: 2026-09-21 13:50:00.000000

`replies.thread_id` был обязателен, и это значило, что ответ, который
не удалось соотнести с нашим письмом, некуда положить.

Такие ответы будут: метка едет в поле «кому» и обычно доходит, заголовки
цепочки теряются пересылками и корпоративными шлюзами регулярно, а иногда
донор пишет заново на общий ящик. Выброшенный ответ выглядит как «донор
не ответил» — и это самый дорогой способ потерять донора, потому что
искать причину будут в лестнице контактов.

Теперь диалога может не быть. Такой ответ виден отдельно, и его
привязывает человек.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b48c2f7a19e5'
down_revision: Union[str, Sequence[str], None] = 'a71e3d9c05b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('replies', 'thread_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DELETE FROM replies WHERE thread_id IS NULL')
    op.alter_column('replies', 'thread_id', existing_type=sa.Integer(), nullable=False)
