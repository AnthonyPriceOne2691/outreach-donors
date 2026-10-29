"""очередь загрузки снята человеком — событие журнала

Revision ID: 409decc6a434
Revises: 5649d5547a41
Create Date: 2026-10-29 13:05:00.000000

Очередь загрузки (`crawl_backlog`) несёт до десяти тысяч обходов вперёд — снять её можно одной
кнопкой, и в журнале должно быть видно, кто и сколько снял.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "409decc6a434"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5649d5547a41"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'crawl_backlog_dropped'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
