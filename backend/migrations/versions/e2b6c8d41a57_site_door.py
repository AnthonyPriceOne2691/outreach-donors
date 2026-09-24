"""дверь сайта для авторов и рекламодателей — признак для очереди

Revision ID: e2b6c8d41a57
Revises: d7f3a2c91e64
Create Date: 2026-09-24 18:00:00.000000

Меню главной («Advertise», «Write for us») судья смотрел и забывал: оно
меняло вердикт только спорным доменам и в базу не ложилось. Теперь где
сайт зовёт авторов — отдельная колонка домена, и очередь рассмотрения
ставит таких первыми. NULL — не смотрели; пустая строка — двери нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b6c8d41a57"
down_revision: Union[str, Sequence[str], None] = "d7f3a2c91e64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("domains", sa.Column("site_door", sa.String(length=256), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("domains", "site_door")
