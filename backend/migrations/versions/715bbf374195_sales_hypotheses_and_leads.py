"""продажи: гипотезы и лиды

Revision ID: 715bbf374195
Revises: 7baf5c71fce2
Create Date: 2026-10-01 20:00:00.000000

Первые таблицы модуля «Продажи». `sales_hypotheses` — кому и зачем пишем;
тексты гипотез заводятся командой, а не здесь: репозиторий публичный.
`sales_leads` — человек с именем, должностью и компанией.

**Ключи выбраны по тому, что удаляет код доноров.** Адрес с карточки донора
удаляется, а строка `contacts` одна на домен и адрес: ссылка лида на неё
обнуляется, сам адрес лид хранит у себя. Домен и гипотезу с лидом удалить
нельзя: каскад стёр бы лида молча.

Миграция только создаёт — на живой базе данные не трогаются. Откат снимает
и типы перечислений: таблица их с собой не уносит, и без этого повторный
подъём падает на «тип уже существует».
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "715bbf374195"
down_revision: Union[str, Sequence[str], None] = "7baf5c71fce2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stamps() -> list[sa.Column]:
    """`created_at` и `updated_at` примеси `TimestampedMixin`."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sales_hypotheses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "sales_leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column(
            "source", sa.Enum("import", "referral", name="sales_lead_source"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("new", "ready", "rejected", name="sales_lead_status"),
            nullable=False,
        ),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sales_leads_hypothesis", "sales_leads", ["hypothesis_id"], unique=False)
    op.create_index("idx_sales_leads_domain", "sales_leads", ["domain_id"], unique=False)
    op.create_index("idx_sales_leads_contact", "sales_leads", ["contact_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_sales_leads_contact", table_name="sales_leads")
    op.drop_index("idx_sales_leads_domain", table_name="sales_leads")
    op.drop_index("idx_sales_leads_hypothesis", table_name="sales_leads")
    op.drop_table("sales_leads")
    op.drop_table("sales_hypotheses")
    sa.Enum(name="sales_lead_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="sales_lead_source").drop(op.get_bind(), checkfirst=True)
