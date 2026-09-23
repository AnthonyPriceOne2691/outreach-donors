"""текст первого письма у рассылки

Revision ID: a3f19c6d2e57
Revises: e2b6f9a04c71
Create Date: 2026-09-23 23:30:00.000000

Текст первого письма правится на экране перед созданием рассылки, и
рассылка хранит утверждённый вариант целиком. Пусто — шаблон из кода:
так остаются рассылки, заведённые до этой правки.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f19c6d2e57"
down_revision: Union[str, Sequence[str], None] = "e2b6f9a04c71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("campaigns", sa.Column("letter_template", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("campaigns", "letter_template")
