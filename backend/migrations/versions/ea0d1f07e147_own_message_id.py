"""свой Message-ID письма: якорь цепочки и запасная привязка ответа

Revision ID: ea0d1f07e147
Revises: b7d3e9a15c42
Create Date: 2026-09-28 12:00:00.000000

До этой колонки якорем цепочки служил номер письма у платформы
(`provider_message_id`, вида `W8Rx7c2bQvuh3sVUkwV8Hw`). Получатель его
не видит: письмо приходит к нему с другим `Message-ID`. Добивки поэтому
не ложились в ветку, а ответ без метки не находил своего письма.

Теперь идентификатор ставим сами и храним рядом. Уникальный индекс —
запасная привязка ищет письмо по идентификатору из заголовков ответа,
и найти два письма сразу она не должна. Старых записей не заполняем:
боевых писем до этой колонки не было, а у пробных и выдуманных якоря
просто нет — добивка к ним уходит без заголовков цепочки.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ea0d1f07e147"
down_revision: Union[str, Sequence[str], None] = "b7d3e9a15c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("internet_message_id", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_messages_internet_message_id", "messages", ["internet_message_id"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_messages_internet_message_id", table_name="messages")
    op.drop_column("messages", "internet_message_id")
