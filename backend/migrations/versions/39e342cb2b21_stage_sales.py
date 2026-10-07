"""этап продаж — значение типа `stage`

Revision ID: 39e342cb2b21
Revises: 7bfc6c880f0a
Create Date: 2026-10-05 14:00:00.000000

Рассылки, ящики, отписки и прогоны держат этап колонкой типа `stage`, и у
модуля «Продажи» своей оси не будет: письма лидам пойдут общей почтой, учёткой
платформы своего направления (`config.outreach.mail_account`). Пока почта
продажи не ведёт, каждая её ветка отказывает этапу словами
(`backend/features/core/stages.py`).

Только добавление значения: в этой же ревизии оно не используется.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "39e342cb2b21"
down_revision: Union[str, Sequence[str], None] = "7bfc6c880f0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE stage ADD VALUE IF NOT EXISTS 'sales'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями — рассылки, ящики,
    отписки, прогоны, — а это блокировка таблиц на живой базе.
    """
