"""продажи: шаблоны цепочки писем

Revision ID: 723e3ddab31f
Revises: ad4a79bc6000
Create Date: 2026-10-05 14:00:00.000000

Первое письмо продаж и две добивки на русском и английском. Репозиторий
публичный, поэтому тексты писем живут данными в этой таблице, а не файлами
рядом с кодом, как у доноров.

Ключ — набор, шаг и язык; набор — гипотеза или общий (`hypothesis_id` пуст).
Пустой номер гипотезы — тоже значение ключа (`NULLS NOT DISTINCT`, Postgres 15+):
без этого база пустила бы два общих шаблона одного шага. Тема есть только
у первого письма — добивки идут в той же переписке; это держит проверка.

Миграция только создаёт: строк не заводит, на живой базе ничего не трогает.
Откат снимает таблицу — своего типа у неё нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "723e3ddab31f"
down_revision: Union[str, Sequence[str], None] = "ad4a79bc6000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "sales_chain_templates"


def _now(name: str) -> sa.Column:
    """Время заведения и правки ставит база, как у примеси моделей."""
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Таблица шаблонов: набор, шаг, язык, тема, тело в зонах, включён ли, кто правил."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=True),
        sa.Column("step", sa.SmallInteger(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("step BETWEEN 1 AND 3", name="ck_sales_chain_templates_step"),
        sa.CheckConstraint(
            "(step = 1) = (subject IS NOT NULL)", name="ck_sales_chain_templates_subject"
        ),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "hypothesis_id",
            "step",
            "language",
            name="uq_sales_chain_templates_key",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    """Снять таблицу шаблонов."""
    op.drop_table(TABLE)
