"""ответ: на какие адреса пришёл и почему не привязан

Revision ID: 7baf5c71fce2
Revises: 5946299ccd9a
Create Date: 2026-09-28 18:00:00.000000

Непривязанный ответ сохранялся, но показать человеку было нечего, кроме
текста: адрес, на который он пришёл, не хранился, а причину приём знал
и забывал. Отличить «ответ на пробное письмо» (метка на письмо №0) от
«метки нет» и от «подпись метки не сошлась» по сохранённому было нельзя.

**Причина пишется при приёме, а не выводится потом.** Заголовков цепочки
у ответа нет, а к минуте, когда смотрит человек, секрет могли сменить,
а письмо — удалить: пересчёт объяснял бы не то, что случилось.

Старые ответы остаются с пустыми полями — адреса и причины у них не было,
и выдумывать их миграция не берётся; экран так и говорит: «не записано».
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7baf5c71fce2"
down_revision: Union[str, Sequence[str], None] = "5946299ccd9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "replies",
        sa.Column("to_addresses", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("replies", sa.Column("unbound_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("replies", "unbound_reason")
    op.drop_column("replies", "to_addresses")
