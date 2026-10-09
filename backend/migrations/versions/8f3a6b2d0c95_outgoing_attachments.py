"""вложения наших писем: файлы к ответу в переписке

Revision ID: 8f3a6b2d0c95
Revises: 260e2efdd0c6
Create Date: 2026-10-09 12:00:00.000000

До этой миграции наше письмо уходило только текстом: ответить собеседнику прайсом,
медиакитом или договором файлом было нельзя ничем, кроме своей почты мимо системы.
Файл приходит раньше письма — человек прикладывает его, пока пишет ответ, — поэтому
строка живёт с перепиской (`thread_id`), а письмо (`message_id`) получает при ответе.

Таблица новая и пустая — индексы обычные, без CONCURRENTLY. Откат удаляет таблицу
вместе с файлами: приложенные к ушедшим письмам файлы другой копии у нас не имеют.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a6b2d0c95"
down_revision: Union[str, Sequence[str], None] = "260e2efdd0c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица вложений наших писем и два индекса: по переписке и по письму."""
    op.create_table(
        "outgoing_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_outgoing_attachments_thread_id", "outgoing_attachments", ["thread_id"], unique=False
    )
    op.create_index(
        "idx_outgoing_attachments_message_id", "outgoing_attachments", ["message_id"], unique=False
    )


def downgrade() -> None:
    """Снять индексы и таблицу — вместе с файлами."""
    op.drop_index("idx_outgoing_attachments_message_id", table_name="outgoing_attachments")
    op.drop_index("idx_outgoing_attachments_thread_id", table_name="outgoing_attachments")
    op.drop_table("outgoing_attachments")
