"""сроки добивок задаются рассылкой

Revision ID: d5a91c37e8b4
Revises: a71e3c9d4b02
Create Date: 2026-09-21 22:40:00.000000

Каденция цепочки — свойство рассылки, а не сервиса. Настройка
`OUTREACH_FOLLOWUP_DAYS` остаётся умолчанием для новой рассылки, но
сроки подбирают по отклику, и менять их надо, не трогая уже идущие
цепочки: у письма, отправленного вчера, срок посчитан вчерашним
правилом, и переписывать его задним числом нельзя.

Пусто в колонке — рассылка заведена до этой правки; такая цепочка
берёт умолчание настроек.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5a91c37e8b4"
down_revision: Union[str, Sequence[str], None] = "a71e3c9d4b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Выборка «кому пора добивку» идёт по уже существующему индексу
    # (status, next_action_at) — своего не заводим: лишний индекс на
    # таблице писем стоит записи на каждом письме.
    op.add_column("campaigns", sa.Column("followup_days", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("campaigns", "followup_days")
