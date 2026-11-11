"""судья сам каждую ночь: полосы автоприменения строками истории и строка шага на дату

Revision ID: 5fcbc5aa3951
Revises: fa5cd4ff98c6
Create Date: 2026-11-11 13:30:00.000000

Советы судьи рекламодателей применяются кнопкой (`crawl/judge_apply.py`); теперь то же идёт само
каждую ночь после круга обходов (`crawl/judge_night.py`).

- `advertiser_judge_bands` — включено ли автоприменение полосы: строка на каждую смену, действует
  последняя, строк нет — выключено. Кто и когда включил — в строке, как у потолков модели.
- `advertiser_judge_nights` — шаг ночи: уникальная дата держит «один раз за ночь», итог разбора и
  применения остаётся следом, по нему же шаг продолжает с места после выкатки.
- значение журнала `advertiser_judge_bands_changed` — включение и выключение полосы. В этой же
  ревизии им не пользоваться: в транзакции, где значение добавлено, Postgres этого не даёт.

Таблицы новые и пустые: до первого включения полосы не меняется ничего. Существующих таблиц
ревизия не трогает — замок не нужен. Откат снимает обе таблицы: автоприменение выключено, а
решения, уже поставленные судьёй, остаются решениями и откатываются партией, как раньше.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5fcbc5aa3951"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "fa5cd4ff98c6"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BANDS = "advertiser_judge_bands"
NIGHTS = "advertiser_judge_nights"


def upgrade() -> None:
    """Таблицы полос и шагов ночи, событие журнала о смене полосы."""
    op.create_table(
        BANDS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        NIGHTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("night", sa.Date(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pass_asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("applied", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'advertiser_judge_bands_changed'")


def downgrade() -> None:
    """Без таблиц шаг не начнётся и полосы выключены. Значение журнала остаётся: из перечисления
    Postgres оно не убирается без пересоздания типа, а лишнее значение ничему не мешает."""
    op.drop_table(NIGHTS)
    op.drop_table(BANDS)
