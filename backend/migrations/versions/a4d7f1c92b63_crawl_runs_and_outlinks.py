"""обход донора и его исходящие ссылки

Revision ID: a4d7f1c92b63
Revises: a4c7e1f90b23
Create Date: 2026-09-22 12:40:00.000000

Две таблицы второго этапа. `crawl_runs` — сам обход: чем кончился,
почему остановился, что успел и чего не хватило каскаду. `outlinks` —
то, ради чего он делался: адрес страницы, адрес ссылки, анкор в двух
формах и пометки `rel`.

Сырого HTML здесь нет намеренно: сто тысяч страниц в месяц — это десятки
гигабайт, которые больше ни разу не понадобятся.

Запись обхода нужна не для истории. Без неё «у донора нет исходящих
ссылок» и «мы не смогли его обойти» выглядят одинаково — нулём строк
в `outlinks`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d7f1c92b63"
down_revision: Union[str, Sequence[str], None] = "a4c7e1f90b23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "ok", "partial", "forbidden", "blocked", "failed", name="crawloutcome"
            ),
            nullable=False,
        ),
        sa.Column(
            "stop_reason",
            sa.Enum(
                "exhausted",
                "max_pages",
                "max_attempts",
                "timeout",
                "unhealthy",
                "robots",
                "no_start",
                name="stopreason",
            ),
            nullable=False,
        ),
        sa.Column("pages_opened", sa.Integer(), nullable=False),
        sa.Column("articles", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("degradation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_crawl_runs_host", "crawl_runs", ["host"], unique=False)
    op.create_index("idx_crawl_runs_created_at", "crawl_runs", ["created_at"], unique=False)

    op.create_table(
        "outlinks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("crawl_run_id", sa.Integer(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("target_host", sa.String(length=255), nullable=False),
        sa.Column("target_root", sa.String(length=255), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("anchor_key", sa.Text(), nullable=False),
        sa.Column("nofollow", sa.Boolean(), nullable=False),
        sa.Column("sponsored", sa.Boolean(), nullable=False),
        sa.Column("ugc", sa.Boolean(), nullable=False),
        sa.Column("in_body", sa.Boolean(), nullable=False),
        sa.Column("root_guessed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_outlinks_run", "outlinks", ["crawl_run_id"], unique=False)
    op.create_index("idx_outlinks_target_root", "outlinks", ["target_root"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_outlinks_target_root", table_name="outlinks")
    op.drop_index("idx_outlinks_run", table_name="outlinks")
    op.drop_table("outlinks")
    op.drop_index("idx_crawl_runs_created_at", table_name="crawl_runs")
    op.drop_index("idx_crawl_runs_host", table_name="crawl_runs")
    op.drop_table("crawl_runs")
    # Типы перечислений таблицы не уносят за собой — снимаем явно,
    # иначе повторный upgrade упадёт на «тип уже существует».
    sa.Enum(name="stopreason").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="crawloutcome").drop(op.get_bind(), checkfirst=True)
