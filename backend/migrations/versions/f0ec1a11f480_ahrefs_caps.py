"""месячный кап юнитов Ahrefs — на экране «Расход», с историей смен

Revision ID: f0ec1a11f480
Revises: 73b250f6c0b5
Create Date: 2026-10-16 12:00:00.000000

Решение владельца 16.10.2026: кап виден на экране «Расход», и учётка с правом
править настройки меняет его сама; видно, кто и когда менял. До этого кап задавала
только переменная окружения `AHREFS_UNITS_CAP`, и смета прогона советовала «поднимите
кап» тому, у кого поднять его было нечем.

Строка на каждую смену: действующий кап — последняя строка, строк нет — умолчание
окружения (`runs/ahrefs_cap.py`). Таблица новая и пустая — действует то же, что
и до миграции. Значение журнала `ahrefs_cap_changed` в миграциях не использовать:
ADD VALUE здесь же, а новым значением Postgres не даёт пользоваться в той
транзакции, где оно добавлено.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0ec1a11f480"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "73b250f6c0b5"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица смен капа и событие журнала о смене."""
    op.create_table(
        "ahrefs_caps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("units_cap", sa.Integer(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("units_cap > 0", name="ck_ahrefs_caps_positive"),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'ahrefs_cap_changed'")


def downgrade() -> None:
    """Снять таблицу — кап снова берётся из окружения. Значение журнала остаётся.

    Из перечисления Postgres значение не убирается без пересоздания типа со всеми
    зависимостями — это блокировка журнала на живой базе, а лишнее значение ничему
    не мешает.
    """
    op.drop_table("ahrefs_caps")
