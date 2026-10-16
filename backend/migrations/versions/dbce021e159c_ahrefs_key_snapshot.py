"""снимок ключа Ahrefs и кап «весь лимит ключа»

Revision ID: dbce021e159c
Revises: f0ec1a11f480
Create Date: 2026-10-16 18:00:00.000000

Решение владельца 16.10.2026: сервис владеет текущей ситуацией по ключу Ahrefs, а не
числами, зашитыми в окружение. Снимок ключа — последний удачный ответ
`limits-and-usage` одной строкой: лимит и расход ключа и пространства, следующее
обнуление, когда спрошено. От него считаются кап (размер ключа) и цикл капа (от
обнуления ключа, а не с первого числа) — `runs/ahrefs_key.py`.

Кап на экране «Расход» получает второй вид — «весь лимит ключа»: пустой `units_cap`.
Таблица снимка новая и пустая: до первого ответа Ahrefs размер ключа неизвестен, и
тратить от него нечего — сторож спрашивает Ahrefs первым же проходом.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dbce021e159c"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f0ec1a11f480"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица снимка ключа; кап смены — пустым, когда он следует за ключом."""
    op.create_table(
        "ahrefs_key_snapshot",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("key_limit", sa.BigInteger(), nullable=True),
        sa.Column("key_used", sa.BigInteger(), nullable=True),
        sa.Column("workspace_limit", sa.BigInteger(), nullable=True),
        sa.Column("workspace_used", sa.BigInteger(), nullable=True),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ahrefs_key_snapshot_single"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("ahrefs_caps", "units_cap", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Снять снимок; смены «весь лимит ключа» прежний код прочесть не может — они уходят,
    и действует прежняя смена с числом, а без неё — умолчание окружения прежнего кода."""
    op.execute("DELETE FROM ahrefs_caps WHERE units_cap IS NULL")
    op.alter_column("ahrefs_caps", "units_cap", existing_type=sa.Integer(), nullable=False)
    op.drop_table("ahrefs_key_snapshot")
