"""версия промпта судьи на домене

Revision ID: c4e8a1f5b203
Revises: b7e2d4c91a05
Create Date: 2026-09-24 10:00:00.000000

Судья v2 различает продажу размещения у себя и на чужих сайтах. Вердикты
v1 остаются на доменах, которых пересуд не коснулся, и точность против
человека без отметки версии считалась бы по смеси двух судей.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1f5b203"
down_revision: Union[str, Sequence[str], None] = "b7e2d4c91a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("domains", sa.Column("judge_version", sa.String(32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("domains", "judge_version")
