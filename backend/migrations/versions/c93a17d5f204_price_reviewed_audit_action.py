"""подтверждение разбора цены — событие журнала

Revision ID: c93a17d5f204
Revises: b48c2f7a19e5
Create Date: 2026-09-21 14:40:00.000000

Цена, положенная в карточку донора руками, — решение с последствиями:
по ней запускается Этап 2 и по ней считается приёмка. У такого решения
должен быть автор, а значит и своё событие в журнале. Чужое событие
(«пороги изменены») сделало бы журнал нечитаемым ровно там, где
по нему будут разбираться.
"""

from typing import Sequence, Union

from alembic import op

revision: str = 'c93a17d5f204'
down_revision: Union[str, Sequence[str], None] = 'b48c2f7a19e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'price_reviewed'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями. Оставленное лишнее
    значение ничему не мешает, а пересоздание типа на живой базе — это
    блокировка таблицы журнала.
    """
