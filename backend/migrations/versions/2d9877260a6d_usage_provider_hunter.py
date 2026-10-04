"""расход: проверка адресов — отдельный провайдер в журнале

Revision ID: 2d9877260a6d
Revises: 95ee6522e0de
Create Date: 2026-10-03 12:10:00.000000

Проверка адреса лида продаж платная, и ключ сервиса общий с соседней
системой: каждая проверка пишется в журнал расхода за своим провайдером,
а не теряется (находка чтения 01.10: траты этого сервиса не писались нигде).

Только добавление значения: в этой же ревизии оно не используется.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "2d9877260a6d"
down_revision: Union[str, Sequence[str], None] = "95ee6522e0de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE usageprovider ADD VALUE IF NOT EXISTS 'hunter'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы расхода на живой базе. Лишнее значение не мешает ничему.
    """
