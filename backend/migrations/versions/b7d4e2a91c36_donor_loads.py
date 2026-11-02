"""загрузки доноров: партия файлом или донор вручную — и домены загрузки

Revision ID: b7d4e2a91c36
Revises: 3b7e1c94d2a6
Create Date: 2026-11-02 13:00:00.000000

Очередь загрузки теряет строку, как только донор ушёл в обход, и от партии не оставалось ничего:
панели рекламодателей нечем считать выборку «партия» и качество загрузок. Две новые таблицы, чужие
не трогаются. Загрузки до этой ревизии не восстанавливаются: панель показывает их обходы без партии.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2a91c36"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "3b7e1c94d2a6"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stamp(name: str) -> sa.Column[Any]:
    """Отметка времени строки: ставит база."""
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    """Загрузка — строкой, её домены — своими строками с индексом по имени."""
    op.create_table(
        "donor_loads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("crawl", sa.Boolean(), nullable=False),
        *(_stamp(name) for name in ("created_at", "updated_at")),
        sa.CheckConstraint("kind IN ('bulk', 'manual')", name="ck_donor_loads_kind"),
    )
    op.create_index("idx_donor_loads_created_at", "donor_loads", ["created_at"])
    op.create_table(
        "donor_load_hosts",
        sa.Column(
            "load_id",
            sa.Integer(),
            sa.ForeignKey("donor_loads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("host", sa.String(length=255), primary_key=True),
    )
    op.create_index("idx_donor_load_hosts_host", "donor_load_hosts", ["host"])


def downgrade() -> None:
    """Обе таблицы — вместе с записанными партиями: восстановить их будет нечем."""
    op.drop_index("idx_donor_load_hosts_host", table_name="donor_load_hosts")
    op.drop_table("donor_load_hosts")
    op.drop_index("idx_donor_loads_created_at", table_name="donor_loads")
    op.drop_table("donor_loads")
