"""адрес донора удалён человеком — событие журнала

Revision ID: b7d3e9a15c42
Revises: e2b6c8d41a57
Create Date: 2026-09-26 20:00:00.000000

Адрес донора теперь вписывают и удаляют с его карточки. Удаляется только
адрес, которому не писали, но и такое удаление меняет, кому уйдёт письмо:
у него должен быть автор и время, как у вписанного (`contact_added`).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b7d3e9a15c42"
down_revision: Union[str, Sequence[str], None] = "e2b6c8d41a57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'contact_removed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе. Лишнее значение не мешает ничему.
    """
