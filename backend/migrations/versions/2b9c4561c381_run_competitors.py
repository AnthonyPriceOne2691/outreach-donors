"""прогон по ссылкам конкурентов

Revision ID: 2b9c4561c381
Revises: b14a5e5368a1
Create Date: 2026-10-20 18:00:00.000000

Вопрос оператора: «указываем сайты конкурентов, и оно парсит их ссылочную массу, отбирает
хорошие площадки». Прогон получает третий вход рядом с ключами — домены конкурентов
и глубину списка (`runs/competitors.py`). Они лежат в строке прогона, как ключи: задаче
хватает номера прогона, а продолжение после смерти воркера берёт отсюда уже полученные
списки и не покупает их второй раз. У прогонов по ключам колонка пуста.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2b9c4561c381"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b14a5e5368a1"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("competitors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "competitors")
