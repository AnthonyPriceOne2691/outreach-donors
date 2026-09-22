"""у записи стоп-листа появляется срок

Revision ID: c4e7a2b95f16
Revises: b8d2f4a7c619
Create Date: 2026-09-22 21:10:00.000000

Требование просит исключать «всех, у кого агентство размещалось за 12 мес.,
и текущих партнёров». Второе бессрочно, первое — скользящее окно, и выразить
его было нечем: обе таблицы хранили только факт записи.

Пусто значит «навсегда». Отписка и жалоба срока не получают никогда — их
заводит не человек, а страница отписки и приём ответов, и поля они
не заполняют.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e7a2b95f16'
down_revision: Union[str, Sequence[str], None] = 'b8d2f4a7c619'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ("suppressions", "supplier_donors"):
        op.add_column(
            table,
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("suppressions", "supplier_donors"):
        op.drop_column(table, "expires_at")
