"""ссылки обхода: чем помечена статья и когда она вышла

Revision ID: b7c3e91d0a52
Revises: e4b1c27a9d53
Create Date: 2026-10-06 16:00:00.000000

Раскрытие рекламы пишется не в ссылке, а в статье — рубрикой в разметке,
разделом в мета-данных, фразой «This post is sponsored by…». Обход хранит
ссылки, а не текст страницы, поэтому снятое со страницы едет вместе
с каждой её ссылкой: скоринг читает его при пересчёте без нового обхода.

Дата статьи — для свежести: размещения покупают сейчас, а по карте сайта
обход приносит и статьи двадцатилетней давности.

Обе колонки пустые у старых обходов: «не смотрели» — это `NULL`, а не
«пометки нет».
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c3e91d0a52"
down_revision: Union[str, Sequence[str], None] = "e4b1c27a9d53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("outlinks", sa.Column("page_label", sa.Text(), nullable=True))
    op.add_column("outlinks", sa.Column("page_published", sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("outlinks", "page_published")
    op.drop_column("outlinks", "page_label")
