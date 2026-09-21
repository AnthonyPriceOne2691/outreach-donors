"""отметки доставки: когда дошло и почему не дошло

Revision ID: f2b6a8c31d47
Revises: e1c4b7d92f08
Create Date: 2026-09-21 22:30:00.000000

До этих двух полей отправка кончалась словом «ушло». Что письмо
не доставлено, выяснялось ответом-отказом, если он приходил, а время
доставки не знал никто — при том что между «ушло» и «дошло» проходят
минуты, а иногда и целый отказ принимающего сервера.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2b6a8c31d47'
down_revision: Union[str, Sequence[str], None] = 'e1c4b7d92f08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('messages', sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('messages', sa.Column('failure_reason', sa.String(length=256), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'failure_reason')
    op.drop_column('messages', 'delivered_at')
