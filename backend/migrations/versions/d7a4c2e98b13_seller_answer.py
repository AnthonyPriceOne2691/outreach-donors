"""ответ донора: продаёт ли он размещение

Revision ID: d7a4c2e98b13
Revises: c5d2a8e61f47
Create Date: 2026-09-23 20:00:00.000000

Для гест-постинга главный вопрос письма — продаёт ли сайт размещение, и
ответ на него до сих пор терялся: «не продаём» падало в ручную очередь
как «цена не распознана». Теперь разбор отдаёт ответ отдельным полем
(`replies.placement`), а на домен ложится ответ самого донора — отдельно
от судьи и от человека, чтобы по расхождению с ними считалась точность
отбора в главном.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7a4c2e98b13"
down_revision: Union[str, Sequence[str], None] = "c5d2a8e61f47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("replies", sa.Column("placement", sa.String(16), nullable=True))
    op.add_column("domains", sa.Column("seller_answer", sa.String(16), nullable=True))
    op.add_column(
        "domains", sa.Column("seller_answer_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("domains", sa.Column("seller_answer_reply_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("domains", "seller_answer_reply_id")
    op.drop_column("domains", "seller_answer_at")
    op.drop_column("domains", "seller_answer")
    op.drop_column("replies", "placement")
