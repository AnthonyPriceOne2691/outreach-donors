"""вложения ответа: текст, прочитанный из файла, — для разбора цены и для экрана

Revision ID: 5d2c8e1a9f47
Revises: 8f3a6b2d0c95
Create Date: 2026-10-09 12:00:00.000000

Прайс приходит файлом, а цену модель брала только из текста письма. Текст файла
читается один раз — разбором цены или первым показом на экране — и ложится к
вложению: модель и человек видят одно и то же, и файл не разбирается заново.
Три колонки без значений по умолчанию: прежние вложения остаются «не читаны»
и читаются по первому запросу экрана. Строк миграция не трогает.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5d2c8e1a9f47"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "8f3a6b2d0c95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Текст файла, почему его нет — и когда файл читали."""
    op.add_column("reply_attachments", sa.Column("text", sa.Text(), nullable=True))
    op.add_column("reply_attachments", sa.Column("text_note", sa.Text(), nullable=True))
    op.add_column(
        "reply_attachments",
        sa.Column("text_read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Снять колонки: вложения снова только скачиваются, а прайс файлом модель не видит."""
    op.drop_column("reply_attachments", "text_read_at")
    op.drop_column("reply_attachments", "text_note")
    op.drop_column("reply_attachments", "text")
