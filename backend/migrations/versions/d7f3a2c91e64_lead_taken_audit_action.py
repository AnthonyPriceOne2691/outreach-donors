"""лид взят в работу — событие журнала

Revision ID: d7f3a2c91e64
Revises: c4e8a1f5b203
Create Date: 2026-09-24 17:00:00.000000

Ответ рекламодателя на оффер Этапа 2 — лид: его не разбирают как цену,
а берут в работу. Кто и когда взял — решение человека, и журнал его пишет.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d7f3a2c91e64"
down_revision: Union[str, Sequence[str], None] = "c4e8a1f5b203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'lead_taken'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе.
    """
