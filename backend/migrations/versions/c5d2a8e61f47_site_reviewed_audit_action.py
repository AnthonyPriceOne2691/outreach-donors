"""решение человека о типе сайта — событие журнала

Revision ID: c5d2a8e61f47
Revises: b3e9f1c47d20
Create Date: 2026-09-23 18:00:00.000000

Решение на экране отбора меняет, кому уйдёт письмо: во включённом судье
прямо, в наблюдении через следующий прогон. Такие решения журнал пишет.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c5d2a8e61f47"
down_revision: Union[str, Sequence[str], None] = "b3e9f1c47d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'site_reviewed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе.
    """
