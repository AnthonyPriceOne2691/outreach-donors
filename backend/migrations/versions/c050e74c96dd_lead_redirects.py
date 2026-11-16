"""переадресация в диалоге рекламодателя: решение по ответу «пишите на …»

Revision ID: c050e74c96dd
Revises: 5fcbc5aa3951
Create Date: 2026-11-16 12:00:00.000000

Рекламодатель отвечает «да, но напишите вот на этот адрес» — до этой ревизии диалог стоял «лид —
ждёт человека», и лид терялся. Теперь проход сам шлёт то же первое письмо на названный адрес
в ту же переписку (`replies/lead_redirect.py`, `letters/redirect_letter.py`).

- `lead_redirects` — строка на ответ, поставленный на разбор: состояние, выбранный адрес, почему
  не переслали или чего письмо ждёт, пометка о чужом домене, письмо, токены и ответ модели.
  Одна строка на ответ — уникальный `reply_id`: повтор прохода второй раз модель не зовёт.

Таблица новая и пустая; существующих ревизия не трогает — замок не нужен. Откат снимает таблицу:
письма, уже заведённые переадресацией, остаются письмами переписки (шаг 300), а ответы — ответами.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c050e74c96dd"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5fcbc5aa3951"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "lead_redirects"


def upgrade() -> None:
    """Таблица решений переадресации с индексами по переписке и состоянию."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reply_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("tried_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["reply_id"], ["replies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reply_id"),
    )
    op.create_index("idx_lead_redirects_thread", TABLE, ["thread_id"])
    op.create_index("idx_lead_redirects_state", TABLE, ["state"])


def downgrade() -> None:
    """Без таблицы приём не ставит ответы на разбор, а проход не идёт; письма остаются."""
    op.drop_index("idx_lead_redirects_state", table_name=TABLE)
    op.drop_index("idx_lead_redirects_thread", table_name=TABLE)
    op.drop_table(TABLE)
