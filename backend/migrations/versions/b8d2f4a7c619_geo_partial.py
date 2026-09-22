"""разбивка по странам бывает неполной

Revision ID: b8d2f4a7c619
Revises: a4c7e91b58d3
Create Date: 2026-09-22 16:20:00.000000

Верхняя страна приезжает вместе с метриками за 10 юнитов на домен, тогда
как отдельный запрос по странам стоит 55 и идёт по одному домену. Когда
верхняя страна и есть целевая, вердикт готов без него — но в базе остаётся
одна строка разбивки вместо пяти.

Одна строка у полного ответа (домен с трафиком из единственной страны)
и одна строка у дешёвого пути — разные вещи, а по длине списка их
не отличить. Без этого флага карточка донора выдавала бы вторую за первую.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d2f4a7c619'
down_revision: Union[str, Sequence[str], None] = 'a4c7e91b58d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "donors",
        sa.Column("geo_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("donors", "geo_partial")
