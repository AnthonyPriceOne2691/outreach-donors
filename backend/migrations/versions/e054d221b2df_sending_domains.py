"""домены рассылки — дневной лимит домена, выдержка нового домена, пауза

Revision ID: e054d221b2df
Revises: 75242c2ed7ba
Create Date: 2026-10-07 18:00:00.000000

Репутация живёт у домена, а не у ящика: два ящика по двадцать писем на одном
домене — это сорок писем с домена. Строка домена не обязательна: домен без неё
пишет так, как пишут его ящики (у доноров строк нет — поведение прежнее).

Только новая таблица. Значение `sales` типа `stage` здесь не используется ни
умолчанием, ни строкой, ни проверкой: оно добавлено ревизией 39e342cb2b21 в той
же транзакции прогона, и использование дало бы «unsafe use of new value». Домены
заводит команда консоли, а не миграция.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e054d221b2df"
down_revision: Union[str, Sequence[str], None] = "75242c2ed7ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Время записи строки — как у всех таблиц (`TimestampedMixin`).
_NOW = sa.text("now()")


def upgrade() -> None:
    """Таблица доменов рассылки."""
    op.create_table(
        "sending_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(length=253), nullable=False, unique=True),
        # Тип этапа уже есть у ящиков и рассылок — второй не заводится.
        sa.Column("stage", postgresql.ENUM(name="stage", create_type=False), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint("daily_limit >= 0", name="ck_sending_domains_daily_limit"),
        sa.Column("pause_reason", sa.String(length=128), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("young_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )


def downgrade() -> None:
    """Снять таблицу: лимиты доменов уходят, ящики пишут, как писали до неё."""
    op.drop_table("sending_domains")
