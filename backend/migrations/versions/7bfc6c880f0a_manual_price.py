"""цена руками: откуда последняя цена донора и донор, заведённый вручную

Revision ID: 7bfc6c880f0a
Revises: cbc5aadf4fc2
Create Date: 2026-10-07 00:49:18.000000

Требование Этапа 2: «по кому запускаем — только доноры с известной ценой
(из базы или заведённые вручную)». Цена попадала к донору только из
разобранного ответа на письмо Этапа 1, а агентство знает цены многих сайтов
само. Цена, указанная человеком, ложится в те же поля `last_price*`; рядом —
откуда она, заметка «откуда цена» и кто её указал. Донор, заведённый вручную,
помечается тем, кто его завёл.

У прежних строк все четыре колонки пустые, и это правда, а не пропуск: пустой
источник — цена из ответа (до 07.10.2026 другого пути не было), пустой
`entered_by` — донор из прогона.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7bfc6c880f0a"
down_revision: Union[str, Sequence[str], None] = "cbc5aadf4fc2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("donors", sa.Column("last_price_source", sa.String(length=16), nullable=True))
    op.add_column("donors", sa.Column("last_price_note", sa.String(length=200), nullable=True))
    op.add_column("donors", sa.Column("last_price_by", sa.String(length=255), nullable=True))
    op.add_column("donors", sa.Column("entered_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("donors", "entered_by")
    op.drop_column("donors", "last_price_by")
    op.drop_column("donors", "last_price_note")
    op.drop_column("donors", "last_price_source")
