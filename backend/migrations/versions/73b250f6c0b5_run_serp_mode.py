"""режим покупки выдачи у прогона доноров

Revision ID: 73b250f6c0b5
Revises: 07bcb0eef6a1
Create Date: 2026-10-14 20:00:00.000000

Слово владельца 14.10.2026: в первую очередь скорость — по умолчанию быстрый
режим выдачи, медленный и дешёвый выбирают на запуске (`serp/modes.py`). Режим
пишется в строку прогона при постановке: задача покупает выдачу тем режимом,
который человек видел в смете, даже если умолчание сервиса сменят между
нажатием и стартом. У прежних прогонов колонка пуста — они шли отложенной
очередью, а продолжение такого прогона берёт умолчание сервиса.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "73b250f6c0b5"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "07bcb0eef6a1"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("serp_mode", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "serp_mode")
