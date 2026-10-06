"""обход донора — задачей очереди: состояние, задача, продолжения, чекпоинт

Revision ID: fc721be3d031
Revises: c41f7a2e9b86
Create Date: 2026-10-06 18:00:00.000000

До этой ревизии строка обхода появлялась одна, в конце, вместе со всеми
ссылками: обход шёл из консоли и записывался, только если доходил до конца.
Обход в 1 000 страниц идёт до получаса, и теперь он — задача очереди: строка
заводится постановкой, ссылки ложатся пачками по ходу, а у выкатки и смерти
воркера есть с чего продолжить.

Исход и причина остановки становятся пустыми у незаконченного обхода: у него
их ещё нет. Все прежние строки — законченные обходы (`done`).

Замок «один обход донора за раз» — уникальный индекс по хосту среди
незаконченных: гонку двух нажатий держит база.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fc721be3d031"
down_revision: Union[str, Sequence[str], None] = "c41f7a2e9b86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS = sa.Enum("queued", "running", "done", "stopped", name="crawlstatus")
_OUTCOME = sa.Enum("ok", "partial", "forbidden", "blocked", "failed", name="crawloutcome")
_STOP = sa.Enum(
    "exhausted",
    "max_pages",
    "max_attempts",
    "timeout",
    "unhealthy",
    "robots",
    "no_start",
    name="stopreason",
)


def upgrade() -> None:
    """Upgrade schema."""
    _STATUS.create(op.get_bind(), checkfirst=True)
    # Умолчание — только чтобы заполнить прежние строки: дальше состояние
    # всегда называет код, и молчаливое «done» у новой строки было бы враньём.
    op.add_column(
        "crawl_runs", sa.Column("status", _STATUS, nullable=False, server_default="done")
    )
    op.alter_column("crawl_runs", "status", server_default=None)
    op.add_column("crawl_runs", sa.Column("job_id", sa.String(length=64), nullable=True))
    op.add_column("crawl_runs", sa.Column("requested_by", sa.String(length=255), nullable=True))
    op.add_column(
        "crawl_runs", sa.Column("resumes", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "crawl_runs", sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.alter_column("crawl_runs", "outcome", existing_type=_OUTCOME, nullable=True)
    op.alter_column("crawl_runs", "stop_reason", existing_type=_STOP, nullable=True)
    op.create_index(
        "uq_crawl_runs_active_host",
        "crawl_runs",
        ["host"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_crawl_runs_active_host",
        table_name="crawl_runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    # До этой ревизии незаконченных обходов не было: строка появлялась
    # с исходом. Оставшиеся без исхода записываются как «не начат» —
    # честнее, чем выдумывать им удачу.
    op.execute("UPDATE crawl_runs SET outcome = 'failed' WHERE outcome IS NULL")
    op.execute("UPDATE crawl_runs SET stop_reason = 'no_start' WHERE stop_reason IS NULL")
    op.alter_column("crawl_runs", "stop_reason", existing_type=_STOP, nullable=False)
    op.alter_column("crawl_runs", "outcome", existing_type=_OUTCOME, nullable=False)
    op.drop_column("crawl_runs", "checkpoint")
    op.drop_column("crawl_runs", "resumes")
    op.drop_column("crawl_runs", "requested_by")
    op.drop_column("crawl_runs", "job_id")
    op.drop_column("crawl_runs", "status")
    _STATUS.drop(op.get_bind(), checkfirst=True)
