"""чистка прогонов и пробных ответов — событие журнала

Revision ID: e4b1c27a9d53
Revises: 27a07f0ca34b
Create Date: 2026-10-06 12:00:00.000000

Чистка удаляет прогоны, домены с адресами и пробные ответы — вернуть их
нечем. В журнале действий должно остаться, что ушло, сколько и кто удалил.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e4b1c27a9d53"
down_revision: Union[str, Sequence[str], None] = "27a07f0ca34b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'data_pruned'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
