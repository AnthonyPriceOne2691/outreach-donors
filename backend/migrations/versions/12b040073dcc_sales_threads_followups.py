"""продажи: тексты добивок у переписки лида — собранные с первым письмом

Revision ID: 12b040073dcc
Revises: 1045a2d42013
Create Date: 2026-10-12 00:30:00.000000

`sales_threads.followups` — тексты добивок (шаги 2–3) с зонами `rewrite`, которые сборка
очереди продаж готовит вместе с первым письмом: текст шага, отличие, принятые зоны и отличие
каждой попытки — с версией цепочки, которой собраны (`sales/followup_texts.py`). Отправка
добивки берёт сохранённый текст той же версии цепочки, иначе — шаблон шага, как раньше.
Пусто — тексты не собирались (переписка старше колонки или у добивок нет зон `rewrite`):
добивка уходит шаблоном.

Только новая пустая колонка: строк миграция не трогает. Откат снимает колонку — добивки
снова уходят шаблоном шага, как до неё.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "12b040073dcc"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "1045a2d42013"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "sales_threads"


def upgrade() -> None:
    """Пустая колонка текстов добивок у переписки лида."""
    op.add_column(TABLE, sa.Column("followups", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Снять колонку: добивки уходят шаблоном шага."""
    op.drop_column(TABLE, "followups")
