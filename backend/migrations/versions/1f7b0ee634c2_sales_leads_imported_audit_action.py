"""продажи: загрузка базы лидов — событие журнала

Revision ID: 1f7b0ee634c2
Revises: 715bbf374195
Create Date: 2026-10-02 18:00:00.000000

Загрузка базы решает, кому продажи напишут: у неё должны быть автор, источник
и число загруженных строк. По журналу потом ищут, откуда взялся лид.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "1f7b0ee634c2"  # pragma: allowlist secret — номер ревизии, а не ключ
down_revision: Union[str, Sequence[str], None] = "715bbf374195"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'sales_leads_imported'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы журнала на живой базе. Лишнее значение не мешает ничему.
    """
