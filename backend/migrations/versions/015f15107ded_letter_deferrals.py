"""отсрочка доставки у письма: когда, какая попытка, что ответил сервер и почему

Revision ID: 015f15107ded
Revises: 409decc6a434
Create Date: 2026-10-29 17:20:00.000000

Платформа присылает «отсрочку» (`deferred`), когда сервер получателя не принял письмо и она
повторяет — до 72 часов. Отсрочку принимали и отбрасывали: статус «ушло» она не меняет, у письма
не оставалось ничего, и человек шесть часов видел «отправлено» без исхода. Теперь у письма —
последняя отсрочка: время последней и первой, номер попытки, ответ сервера (обрезанный) и вид
причины словами (`letters/delivery_trouble.py`).

Пять пустых колонок — без значения по умолчанию: Postgres добавляет их без перезаписи таблицы.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015f15107ded"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "409decc6a434"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[sa.Column[Any]]:
    """Отсрочка письма: время последней и первой, номер попытки, ответ сервера, вид причины.
    Свежие колонки на каждый вызов: колонка, отданная `add_column`, принадлежит своей таблице."""
    return [
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferred_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferred_attempts", sa.Integer(), nullable=True),
        sa.Column("deferred_reason", sa.String(length=256), nullable=True),
        sa.Column("deferred_kind", sa.String(length=32), nullable=True),
    ]


def upgrade() -> None:
    """Пять пустых колонок отсрочки у письма."""
    for column in _columns():
        op.add_column("messages", column)


def downgrade() -> None:
    """Колонки отсрочки — в обратном порядке."""
    for column in reversed(_columns()):
        op.drop_column("messages", column.name)
