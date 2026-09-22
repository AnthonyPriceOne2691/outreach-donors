"""решение человека по кандидату в рекламодатели — событие журнала

Revision ID: c8a1e5d73b20
Revises: b7e2c4a81f95
Create Date: 2026-09-22 15:20:00.000000

Подтверждение или отклонение пограничного кандидата меняет, кому уйдёт
письмо. Такие решения журнал пишет: «откуда у нас этот адресат» спросит
либо сам адресат, либо юрист.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8a1e5d73b20"
down_revision: Union[str, Sequence[str], None] = "b7e2c4a81f95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'advertiser_reviewed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе.
    """
