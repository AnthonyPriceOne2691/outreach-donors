"""переписка: наш ответ на ответ — письмо знает, на что оно отвечает

Revision ID: 8dbc46c01caf
Revises: 2d9877260a6d
Create Date: 2026-10-04 23:00:00.000000

До 04.10.2026 ответить донору или рекламодателю из переписки было нельзя:
уходили только первое письмо и добивки. Ответ — то же письмо в `messages`,
но со ссылкой на входящий ответ, на который он написан: по ней экран ставит
его под нужным ответом, а агент переписки (следующий срез) — свой черновик.

Только добавление: колонка пустая, данные не трогаются.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8dbc46c01caf"
down_revision: Union[str, Sequence[str], None] = "2d9877260a6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Колонка «на что отвечает», её связь с ответами и индекс для экрана."""
    op.add_column("messages", sa.Column("answers_reply_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_messages_answers_reply",
        "messages",
        "replies",
        ["answers_reply_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_messages_answers_reply_id", "messages", ["answers_reply_id"])


def downgrade() -> None:
    """Снять индекс, связь и колонку — в обратном порядке."""
    op.drop_index("idx_messages_answers_reply_id", table_name="messages")
    op.drop_constraint("fk_messages_answers_reply", "messages", type_="foreignkey")
    op.drop_column("messages", "answers_reply_id")
