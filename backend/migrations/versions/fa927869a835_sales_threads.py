"""продажи: диалог продаж и его лид — явная связь

Revision ID: fa927869a835
Revises: a9e76c0eb5b0
Create Date: 2026-10-07 01:00:00.000000

`sales_threads` — одна строка на диалог продаж: чей лид, каким набором цепочки и
на каком языке написано первое письмо, какой версией цепочки. Строки `contacts`
у диалога продаж нет (адрес лида живёт у лида), и поиск лида по домену и адресу
двух лидов одной компании не различит — связь заводит сборка очереди продаж.

Диалог уходит — связь с ним (каскад: чистка удаляет только липовую переписку).
Лида и гипотезу, на которые ссылается диалог, удалить нельзя (`RESTRICT`).

Миграция только создаёт: строк не заводит. Своего типа у таблицы нет — откат
снимает таблицу, и повторный подъём чистый.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fa927869a835"
down_revision: Union[str, Sequence[str], None] = "a9e76c0eb5b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "sales_threads"


def upgrade() -> None:
    """Таблица связи диалога продаж с лидом и набором цепочки."""
    moment = sa.DateTime(timezone=True)  # когда заведена и правлена — время сервера
    op.create_table(
        TABLE,
        sa.Column("thread_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("chain_hypothesis_id", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("chain_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", moment, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", moment, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["chain_hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("thread_id"),
        sa.UniqueConstraint("lead_id", name="uq_sales_threads_lead"),
    )
    op.create_index(
        "idx_sales_threads_chain_hypothesis", TABLE, ["chain_hypothesis_id"], unique=False
    )


def downgrade() -> None:
    """Снять таблицу связи."""
    op.drop_index("idx_sales_threads_chain_hypothesis", table_name=TABLE)
    op.drop_table(TABLE)
