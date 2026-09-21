"""стоп-лист руками — два события журнала

Revision ID: e1c4b7d92f08
Revises: d5a91c37e8b4
Create Date: 2026-09-21 20:10:00.000000

Запись в стоп-листе решает, придёт ли донору письмо, и снятие записи
об отписке — это разрешение написать тому, кто просил не писать.
У обоих действий должен быть автор и время, а чужое событие сделало бы
журнал нечитаемым ровно там, где по нему будут разбираться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = 'e1c4b7d92f08'
down_revision: Union[str, Sequence[str], None] = 'd5a91c37e8b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'suppression_added'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'suppression_removed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе. Лишнее значение не мешает ничему.
    """
