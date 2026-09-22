"""рекламодатели и стоп-лист доноров-поставщиков

Revision ID: d3f6b2a9c418
Revises: c8a1e5d73b20
Create Date: 2026-09-22 16:10:00.000000

`advertisers` — по одной строке на домен. Уникальность домена и есть
правило требования «один рекламодатель — одно письмо, сколько бы страниц
он ни занимал»: оно выражено ограничением базы, а не бережностью кода.

`supplier_donors` — доноры, чьих рекламодателей мы не трогаем: площадки,
где агентство размещалось за последний год, и текущие партнёры. Список
приходит со стороны задачи; пока его нет, таблица пуста, и это видно
числом, а не молчанием.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3f6b2a9c418"
down_revision: Union[str, Sequence[str], None] = "c8a1e5d73b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "advertisers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("donors", sa.Integer(), nullable=False),
        sa.Column("links", sa.Integer(), nullable=False),
        sa.Column("best_donor_host", sa.String(length=255), nullable=True),
        sa.Column("best_page_url", sa.Text(), nullable=True),
        sa.Column("best_anchor", sa.Text(), nullable=True),
        sa.Column("confirmed_by_human", sa.Boolean(), nullable=False),
        # Тип уже создан начальной схемой для доноров: лестница одна
        # на оба этапа, и исход у неё один и тот же. `create_type=False`
        # именно поэтому — иначе миграция падает на живой базе, где тип
        # есть, и проходит на пустой, где его ещё нет.
        sa.Column(
            "contact_status",
            postgresql.ENUM(
                "found",
                "not_found",
                "form_only",
                "no_mx",
                "quota",
                name="contactstatus",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("contact_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id"),
    )
    op.create_index("idx_advertisers_points", "advertisers", ["points"], unique=False)
    op.create_index(
        "idx_advertisers_contact_status", "advertisers", ["contact_status"], unique=False
    )

    op.create_table(
        "supplier_donors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("added_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host"),
    )
    op.create_index("idx_supplier_donors_host", "supplier_donors", ["host"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_supplier_donors_host", table_name="supplier_donors")
    op.drop_table("supplier_donors")
    op.drop_index("idx_advertisers_contact_status", table_name="advertisers")
    op.drop_index("idx_advertisers_points", table_name="advertisers")
    op.drop_table("advertisers")
