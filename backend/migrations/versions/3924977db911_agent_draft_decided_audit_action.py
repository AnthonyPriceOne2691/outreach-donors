"""решение по черновику агента — событие журнала

Revision ID: 3924977db911
Revises: 7ccbaf6d840a
Create Date: 2026-10-06 23:30:00.000000

Черновик агента уходит собеседнику как есть, с правкой — или отклоняется
с причиной. В журнале действий должно быть видно, кто принял текст агента
наружу и кто и почему его отклонил.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "3924977db911"
down_revision: Union[str, Sequence[str], None] = "7ccbaf6d840a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'agent_draft_decided'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
