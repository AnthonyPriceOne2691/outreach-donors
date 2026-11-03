"""роль «Рекламодатели» — значение перечисления ролей

Revision ID: d8a2f6c31e47
Revises: c4f7a19e2d36
Create Date: 2026-11-02 19:30:00.000000

Роль видит только Этап 2 (`access/permissions.py`): аутрич и рекламодатели — разные роли, обе
отдельно от админской. Роль хранится перечислением Postgres, и значение роли — значение в нём.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d8a2f6c31e47"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c4f7a19e2d36"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'advertisers'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями. Лишнее значение не мешает ничему.
    """
