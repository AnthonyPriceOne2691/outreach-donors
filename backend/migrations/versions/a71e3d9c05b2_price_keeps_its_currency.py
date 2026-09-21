"""цена донора хранится с валютой, а не «в долларах»

Revision ID: a71e3d9c05b2
Revises: 9a2f1c6b48d3
Create Date: 2026-09-21 13:30:00.000000

Колонка называлась `last_price_usd`. Конвертации в проекте нет, источника
курсов нет, и в требованиях ни того, ни другого не значится — значит
первый же ответ донора в евро лёг бы в поле «в долларах» как есть.

Второй вариант был не лучше: класть только доллары, а остальное не класть
вовсе. Тогда донор с ценой в евро считался бы «без цены» и в порогах,
и в приёмке, где требуется сто доноров с ценами.

Имя, обещающее конвертацию, обещает работу, которой никто не делал.
Цена теперь хранится как названа, валюта лежит рядом.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a71e3d9c05b2'
down_revision: Union[str, Sequence[str], None] = '9a2f1c6b48d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('donors', 'last_price_usd', new_column_name='last_price')
    op.add_column('donors', sa.Column('last_price_currency', sa.String(length=8), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('donors', 'last_price_currency')
    op.alter_column('donors', 'last_price', new_column_name='last_price_usd')
