"""снимок разбора модели у ответа — для калибровки

Revision ID: e2b6f9a04c71
Revises: d7a4c2e98b13
Create Date: 2026-09-23 22:00:00.000000

Подтверждение человека переписывало поля разбора, и что предлагала модель,
после этого не знал никто. Снимок пишется один раз при разборе и больше не
трогается; по расхождению с решением человека считается, какие поля модель
путает и какая версия промпта лучше.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2b6f9a04c71"
down_revision: Union[str, Sequence[str], None] = "d7a4c2e98b13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("replies", sa.Column("model_parse", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("replies", "model_parse")
