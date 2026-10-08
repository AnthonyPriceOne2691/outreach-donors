"""журнал здоровья ящика: номер события платформы — повтор пачки не удваивает строки

Revision ID: 260e2efdd0c6
Revises: d64e2cd71614
Create Date: 2026-10-08 15:00:00.000000

Платформа доставляет события «хотя бы один раз» и повторяет пачку на любой не-2xx.
Строка журнала, которую пишет само событие (мягкий сигнал, жалоба), получает номер
события у платформы (`sg_event_id`); частичный уникальный индекс по нему держит гонку
двух доставок одной пачки. Таблица маленькая — индекс обычный, без CONCURRENTLY.
Строк миграция не трогает: прежние остаются без номера.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "260e2efdd0c6"
down_revision: Union[str, Sequence[str], None] = "d64e2cd71614"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = sa.text("event_id IS NOT NULL")


def upgrade() -> None:
    """Колонка номера события и уникальный индекс по ней."""
    op.add_column("sender_health", sa.Column("event_id", sa.String(length=100), nullable=True))
    op.create_index(
        "uq_sender_health_event",
        "sender_health",
        ["event_id"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    """Снять индекс и колонку: повтор пачки снова пишет строки второй раз."""
    op.drop_index("uq_sender_health_event", table_name="sender_health", postgresql_where=_WHERE)
    op.drop_column("sender_health", "event_id")
