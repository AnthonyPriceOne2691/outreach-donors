"""продажи: правка базы знаний и отправителя — событие журнала

Revision ID: a51e5688f79e
Revises: c6efe4e5de7e
Create Date: 2026-10-05 01:10:00.000000

Из базы знаний агент пишет живым людям, а подпись и адрес уходят в каждое
письмо продаж: у каждой правки должны быть автор, время и версия базы до
и после. По журналу потом ищут, какая правка сменила версию.

Только добавление значения отдельной ревизией: в транзакции, где значение
добавлено, Postgres не даёт им пользоваться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a51e5688f79e"
down_revision: Union[str, Sequence[str], None] = "c6efe4e5de7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'sales_kb_changed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
