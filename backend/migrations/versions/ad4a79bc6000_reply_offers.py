"""цены списком: все цены из ответа — у ответа и у донора

Revision ID: ad4a79bc6000
Revises: fc721be3d031
Create Date: 2026-10-06 23:30:00.000000

Разбор ответа хранил одну цену — гостевого поста, белую или серую, — а прочие
продукты (вставка ссылки, ссылка на главной, помесячное размещение) и цены для
ниш (казино, крипта) модель упоминала в заметке, которая не хранится. Решение
Anthony 06.10.2026: хранить список всех названных цен и показывать его.

Обе колонки пустые у прежних строк, и это правда, а не пропуск: пусто —
«разобран до списка», `[]` — «разобран, цен не названо». Цены в списке —
строками, как в снимке разбора: `Decimal` в JSON без потерь не ложится.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ad4a79bc6000"
down_revision: Union[str, Sequence[str], None] = "fc721be3d031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "replies", sa.Column("offers", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "donors", sa.Column("last_offers", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("donors", "last_offers")
    op.drop_column("replies", "offers")
