"""продажи: правка цепочки писем — событие журнала

Revision ID: cbc5aadf4fc2
Revises: 723e3ddab31f
Create Date: 2026-10-05 14:05:00.000000

Шаблоны цепочки уходят живым людям: у каждой правки должны быть автор, время
и версия цепочки до и после. По журналу потом ищут, какая правка сменила текст.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "cbc5aadf4fc2"
down_revision: Union[str, Sequence[str], None] = "723e3ddab31f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'sales_chain_changed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
