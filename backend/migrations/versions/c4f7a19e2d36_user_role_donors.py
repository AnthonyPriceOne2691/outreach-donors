"""роль «Только доноры» — значение перечисления ролей

Revision ID: c4f7a19e2d36
Revises: e3b94f10d7a2
Create Date: 2026-11-02 19:00:00.000000

Роль видит только Этап 1 (`access/permissions.py`): другая сторона просила учётку без раздела
рекламодателей. Роль хранится перечислением Postgres, и значение роли — значение в нём.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4f7a19e2d36"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e3b94f10d7a2"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'donors'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями. Лишнее значение не мешает ничему.
    """
