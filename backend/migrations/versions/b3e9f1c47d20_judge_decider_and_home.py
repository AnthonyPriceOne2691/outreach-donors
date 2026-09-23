"""кто вынес вердикт судьи и что сказала главная

Revision ID: b3e9f1c47d20
Revises: a1d7c5e93b62
Create Date: 2026-09-23 16:00:00.000000

Судья стал трёхслойным: правило, модель по выдаче, арбитр по выдаче
и главной. Точность у слоёв разная, и мерить её одним числом значит не
видеть, какой из них ошибается. Отсюда `judge_decided_by`.

`judge_home` — на чём стояло решение со стороны главной: открылась ли она
и какие признаки магазина нашлись. Без него «главная молчала» и «главную
не спрашивали» в базе неотличимы.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b3e9f1c47d20'
down_revision: Union[str, Sequence[str], None] = 'a1d7c5e93b62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("domains", sa.Column("judge_decided_by", sa.String(8), nullable=True))
    op.add_column(
        "domains", sa.Column("judge_home", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("domains", "judge_home")
    op.drop_column("domains", "judge_decided_by")
