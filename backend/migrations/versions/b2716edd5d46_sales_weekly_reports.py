"""продажи: журнал отправки недельного отчёта в группу продаж

Revision ID: b2716edd5d46
Revises: ed31d48a1ec2
Create Date: 2026-10-12 12:00:00.000000

Недельный отчёт продаж за прошлую неделю (пн–вс по Москве) уходит в группу продаж проходом
по расписанию (срез 6.3). Строка журнала — неделя (`week_start`, её понедельник), одна:
уникальный ключ держит «отчёт за неделю уходит один раз» — повтор прохода и второй процесс
строку не задвоят, а отправленный отчёт не пошлют снова. Исход — строкой (`pending`, `sent`,
`undelivered`), а не типом базы: новый исход не требует миграции. Повтор недоставленного — той же
серией, что у сообщений бота продаж (`tries`, `due_at`).

Ссылок на общие таблицы нет: чистка аутрича строк журнала не касается. Миграция только
создаёт: строк не заводит.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2716edd5d46"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "ed31d48a1ec2"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Когда строка заведена и правлена — серверное время, как у примеси моделей.
NOW = sa.text("now()")


def upgrade() -> None:
    """Таблица журнала: неделя, исход, текст отчёта, причина недоставки, повтор."""
    op.create_table(
        "sales_weekly_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tries", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_start", name="uq_sales_weekly_reports_week"),
    )


def downgrade() -> None:
    """Снять таблицу."""
    op.drop_table("sales_weekly_reports")
