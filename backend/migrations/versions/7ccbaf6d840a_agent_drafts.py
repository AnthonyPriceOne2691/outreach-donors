"""агент переписки: черновики ответов — по одному на входящий ответ человека

Revision ID: 7ccbaf6d840a
Revises: 39e342cb2b21
Create Date: 2026-10-04 22:20:00.000000

Агент пишет черновик ответа по настройкам этапа (`agent_settings`), человек
отправляет его как есть, с правкой или отклоняет с причиной. Черновик —
отдельная таблица, а не письмо в `messages`: очередь писем и её счётчики его
не видят. Ссылка на версию настроек объясняет, почему черновик такой; `meta` —
что этап знал до письма и что было в петле правки; решение и ушедший текст —
датасет калибровки агента.

Только добавление: новая таблица и новый тип статуса, существующие данные
не трогаются.
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7ccbaf6d840a"
down_revision: Union[str, Sequence[str], None] = "39e342cb2b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS = sa.Enum(
    "drafted", "skipped", "escalated", "sent", "rejected", name="draftstatus"
)


def _moment(name: str) -> "sa.Column[datetime]":
    """Время записи с умолчанием базы — как у всех таблиц (`TimestampedMixin`)."""
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    """Таблица черновиков: ответ, версия настроек, статус, текст, мета и решение."""
    op.create_table(
        "agent_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reply_id",
            sa.Integer(),
            sa.ForeignKey("replies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "settings_id", sa.Integer(), sa.ForeignKey("agent_settings.id"), nullable=False
        ),
        sa.Column("status", _STATUS, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("final_body", sa.Text(), nullable=True),
        sa.Column("edited", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column(
            "sent_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _moment("created_at"),
        _moment("updated_at"),
        sa.UniqueConstraint("reply_id", name="uq_agent_drafts_reply"),
    )
    op.create_index("idx_agent_drafts_status", "agent_drafts", ["status"])


def downgrade() -> None:
    """Снять таблицу черновиков и тип статуса."""
    op.drop_index("idx_agent_drafts_status", table_name="agent_drafts")
    op.drop_table("agent_drafts")
    _STATUS.drop(op.get_bind(), checkfirst=True)
