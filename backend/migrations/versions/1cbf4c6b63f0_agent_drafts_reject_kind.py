"""агент переписки: вид причины отклонения черновика

Revision ID: 1cbf4c6b63f0
Revises: a9e76c0eb5b0
Create Date: 2026-10-07 15:30:00.000000

У этапа свой список причин отклонения черновика (`AgentStage.reject_reasons`, у
продаж — «неверная ситуация», «факт не из базы» и т. д.). Слова причины
по-прежнему лежат в `reject_reason`; вид — пункт списка или «другое» — ложится
рядом, и калибровка считает отклонения по нему, не читая слов. Причина своими
словами у этапа без строгого списка — колонка пуста.

Только добавление колонки: прежние решения остаются как были.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1cbf4c6b63f0"
down_revision: Union[str, Sequence[str], None] = "a9e76c0eb5b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Вид причины отклонения у черновика."""
    op.add_column("agent_drafts", sa.Column("reject_kind", sa.Text(), nullable=True))


def downgrade() -> None:
    """Снять колонку."""
    op.drop_column("agent_drafts", "reject_kind")
