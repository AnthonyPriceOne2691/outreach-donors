"""продажи: передача лида телемаркетологу — сделка в Kommo и сообщение в Telegram

Revision ID: a9e76c0eb5b0
Revises: 39e342cb2b21
Create Date: 2026-10-06 23:30:00.000000

`sales_handoffs` — одна строка на диалог: ключ — номер диалога, следующий
ответ того же человека ложится примечанием в ту же сделку. Состояния Kommo
и Telegram — два типа: «Kommo повторяем, ссылка на диалог уже ушла» и «сделка
есть, сообщение не доставлено» — обычные исходы, одно значение их не вместит.

Диалог уходит — передача с ним (каскад: чистка удаляет только липовую
переписку). Лида с передачей удалить нельзя (`RESTRICT`): номер сделки
в чужой CRM терять нельзя.

Миграция только создаёт: строк не заводит. Откат снимает и оба типа — таблица
их с собой не уносит, и без этого повторный подъём падает на «тип уже
существует» (урок L5).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9e76c0eb5b0"
down_revision: Union[str, Sequence[str], None] = "39e342cb2b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KOMMO = ("pending", "retry", "unconfirmed", "failed", "done", "off")
TELEGRAM = ("pending", "sent", "undelivered")


def _moment(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _stamps() -> list[sa.Column]:
    """Когда заведена и правлена: серверное время, как у примеси моделей."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    """Таблица передач с двумя типами состояний."""
    op.create_table(
        "sales_handoffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("kommo_lead_id", sa.BigInteger(), nullable=True),
        sa.Column("kommo", sa.Enum(*KOMMO, name="sales_handoff_kommo"), nullable=False),
        sa.Column("telegram", sa.Enum(*TELEGRAM, name="sales_handoff_telegram"), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("noted_reply_id", sa.Integer(), nullable=True),
        sa.Column("notified_link", sa.Text(), nullable=True),
        _moment("notified_at"),
        _moment("due_at"),
        _moment("claimed_at"),
        *_stamps(),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_sales_handoffs_thread"),
    )
    op.create_index("idx_sales_handoffs_lead", "sales_handoffs", ["lead_id"], unique=False)


def downgrade() -> None:
    """Снять таблицу и оба типа состояний."""
    op.drop_index("idx_sales_handoffs_lead", table_name="sales_handoffs")
    op.drop_table("sales_handoffs")
    for name in ("sales_handoff_telegram", "sales_handoff_kommo"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
