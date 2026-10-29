"""очередь загрузки: доноры, загруженные списком и ждущие своего обхода

Revision ID: 5649d5547a41
Revises: 77ff8a338610
Create Date: 2026-10-29 13:00:00.000000

Загрузка доноров таблицей (29.10.2026): до десяти тысяч доноров с ценами за раз, и по галочке —
в обход. Ставить их в очередь обходов всех сразу нельзя: десять тысяч строк «в очереди» закрыли
бы последние обходы, а обход, запущенный человеком, ждал бы месяц за ними. Поэтому загруженные
ждут здесь, а проход разбора отдаёт их обходчикам по мере того, как те освобождаются
(`features/crawl/backlog.py`).

Таблица новая и пустая, существующих ревизия не трогает — замок не нужен. Откат снимает её:
прежний код её не читает, а невыданные доноры остаются донорами с ценой — их можно поставить
в обход кнопкой.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5649d5547a41"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "77ff8a338610"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "crawl_backlog"


def upgrade() -> None:
    """Очередь загрузки: домен — один раз, кто велел обойти."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        *(
            sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host"),
    )


def downgrade() -> None:
    """Снять очередь загрузки: прежний код её не читает."""
    op.drop_table(TABLE)
