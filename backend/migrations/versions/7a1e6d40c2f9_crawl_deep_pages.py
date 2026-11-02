"""свой потолок страниц у обхода и строки очереди загрузки

Revision ID: 7a1e6d40c2f9
Revises: b7d4e2a91c36
Create Date: 2026-11-02 15:00:00.000000

Глубокий обход (`crawl/deep.py`) идёт с потолком в десять тысяч страниц вместо общей тысячи.
Потолок лежит в строке обхода: продолжение после выкатки и разбор мёртвых читают его оттуда, а
обход не становится обычным посреди дороги. В строке очереди загрузки — тоже: глубокие всегда
ждут там. Пусто — обычный обход, поэтому старые строки менять не нужно.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a1e6d40c2f9"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b7d4e2a91c36"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Таблицы с колонкой потолка.
TABLES = ("crawl_runs", "crawl_backlog")


def upgrade() -> None:
    """Колонка потолка у обхода и у строки очереди загрузки."""
    for table in TABLES:
        op.add_column(table, sa.Column("max_pages", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Без колонки глубокий обход продолжится обычным потолком."""
    for table in TABLES:
        op.drop_column(table, "max_pages")
