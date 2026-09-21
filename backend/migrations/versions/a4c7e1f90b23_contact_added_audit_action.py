"""адрес из ручной очереди — событие журнала

Revision ID: a4c7e1f90b23
Revises: f2b6a8c31d47
Create Date: 2026-09-21 23:30:00.000000

Адрес, внесённый человеком вместо лестницы, решает, кому уйдёт письмо,
и приходит он не из кода, а из чужой контактной формы. У такого решения
должен быть автор: через полгода вопрос «откуда у нас этот адрес»
задаст либо донор, либо юрист.
"""

from typing import Sequence, Union

from alembic import op

revision: str = 'a4c7e1f90b23'
down_revision: Union[str, Sequence[str], None] = 'f2b6a8c31d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'contact_added'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе.
    """
