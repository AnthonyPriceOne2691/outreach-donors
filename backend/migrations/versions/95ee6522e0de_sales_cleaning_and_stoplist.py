"""продажи: очистка лидов — причина отказа, вердикт проверки адреса, ручной стоп-лист

Revision ID: 95ee6522e0de
Revises: 1f7b0ee634c2
Create Date: 2026-10-03 12:00:00.000000

Очистка решает, кому продажи напишут, и обязана объяснить каждый отказ:
код причины — для фильтра на экране, слова — для человека. Вердикт
проверяльщика хранится у лида, а не в общих `contacts`: строк `contacts`
загрузка продаж не заводит (решение (а) от 01.10).

`sales_stoplist` — домены и адреса, которым продажи не пишут по решению
человека. Своя таблица, потому что в `suppressions` запись продаж требует
`stage=sales`, а этого значения в `Stage` ещё нет.

Миграция только добавляет: колонки пустые, данные не трогаются. Откат
снимает таблицу, индекс и колонки; типов перечислений она не заводит.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "95ee6522e0de"
down_revision: Union[str, Sequence[str], None] = "1f7b0ee634c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Умолчание сервера для колонок времени примеси `TimestampedMixin`.
_NOW = sa.text("now()")

_LEAD_COLUMNS = (
    sa.Column("rejection_reason", sa.String(length=32), nullable=True),
    sa.Column("cleaning_note", sa.Text(), nullable=True),
    sa.Column("verification_status", sa.String(length=32), nullable=True),
    sa.Column("verification_score", sa.Integer(), nullable=True),
    sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in _LEAD_COLUMNS:
        op.add_column("sales_leads", column)
    op.create_index(
        "idx_sales_leads_status_reason",
        "sales_leads",
        ["status", "rejection_reason"],
        unique=False,
    )
    op.create_table(
        "sales_stoplist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint("(host IS NULL) <> (email IS NULL)", name="ck_sales_stoplist_one_key"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("host"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sales_stoplist")
    op.drop_index("idx_sales_leads_status_reason", table_name="sales_leads")
    for column in reversed(_LEAD_COLUMNS):
        op.drop_column("sales_leads", column.name)
