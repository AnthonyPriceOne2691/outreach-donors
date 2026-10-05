"""агент переписки: настройки по этапам и событие их правки в журнале

Revision ID: d7da62eb8165
Revises: 8dbc46c01caf
Create Date: 2026-10-05 00:40:00.000000

Решение Anthony 04.10.2026: агент пишет черновики ответов собеседнику по
настройкам с экрана — цель, тон, доводы, предел цены, темы, которые он
отдаёт человеку. Настройки у каждого этапа свои и версионируются, как пороги:
черновик должен объясняться той версией, по которой написан.

Новая таблица и новое значение журнала; существующие данные не трогаются.
Значение `agent_settings_changed` в миграциях не использовать: ADD VALUE
здесь же, в одной ревизии с таблицей, и строка журнала с ним в той же
транзакции alembic упала бы «unsafe use of new value».
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7da62eb8165"
down_revision: Union[str, Sequence[str], None] = "8dbc46c01caf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Таблица версий настроек агента и событие журнала о новой версии."""
    op.create_table(
        "agent_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Тип этапа уже есть у кампаний и прогонов — второй не заводится.
        sa.Column(
            "stage",
            postgresql.ENUM("donors", "advertisers", name="stage", create_type=False),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("tone", sa.Text(), nullable=False),
        sa.Column("points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price_limit_usd", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("stop_topics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("stage", "version", name="uq_agent_settings_stage_version"),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'agent_settings_changed'")


def downgrade() -> None:
    """Снять таблицу. Значение журнала остаётся.

    Из перечисления Postgres значение не убирается без пересоздания типа со
    всеми зависимостями — это блокировка журнала на живой базе, а лишнее
    значение ничему не мешает.
    """
    op.drop_table("agent_settings")
