"""продажи: база знаний агента и отправитель

Revision ID: c6efe4e5de7e
Revises: d7da62eb8165
Create Date: 2026-10-05 01:00:00.000000

Агент продаж пишет только из фактов компании, а отправка продаж требует
физического адреса, подписи и имени отправителя. Репозиторий публичный, поэтому
и факты, и подпись живут данными в этих таблицах, а не строками в коде или окружении.

`sales_kb_entries` — запись базы знаний; ключ — вид, язык и заголовок: по нему
повторная загрузка файла узнаёт запись. `sales_settings` — одна строка
отправителя, вторую не пустит проверка `id = 1`.

Миграция только создаёт: на живой базе данные не трогаются, строк она не
заводит. Откат снимает и тип вида записи: таблица его с собой не уносит,
и без этого повторный подъём падает на «тип уже существует» (урок L5).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6efe4e5de7e"
down_revision: Union[str, Sequence[str], None] = "d7da62eb8165"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KINDS = ("brief", "service", "case", "objection", "price_policy", "forbidden", "cta")


def _edited() -> list[sa.Column]:
    """Кто правил и когда: автор строкой, время — серверное, как у примеси моделей."""
    when = [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]
    return [sa.Column("updated_by", sa.String(length=255), nullable=True), *when]


def upgrade() -> None:
    """Таблица записей с типом вида и таблица отправителя."""
    op.create_table(
        "sales_kb_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="sales_kb_kind"), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        *_edited(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "language", "title", name="uq_sales_kb_entries_key"),
    )
    op.create_table(
        "sales_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("sender_name", sa.String(length=128), nullable=True),
        sa.Column("sender_position", sa.String(length=128), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("telegram", sa.String(length=255), nullable=True),
        sa.Column("physical_address", sa.Text(), nullable=True),
        sa.Column("call_link", sa.String(length=512), nullable=True),
        *_edited(),
        sa.CheckConstraint("id = 1", name="ck_sales_settings_one_row"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Снять обе таблицы и тип вида записи."""
    op.drop_table("sales_settings")
    op.drop_table("sales_kb_entries")
    sa.Enum(name="sales_kb_kind").drop(op.get_bind(), checkfirst=True)
