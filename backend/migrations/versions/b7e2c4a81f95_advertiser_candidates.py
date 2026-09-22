"""кандидаты в рекламодатели: балл, вердикт и решение человека

Revision ID: b7e2c4a81f95
Revises: a4d7f1c92b63
Create Date: 2026-09-22 14:10:00.000000

Единица решения — домен, а не ссылка: письмо уходит владельцу один раз,
сколько бы страниц донора он ни занимал. Ссылки остаются в `outlinks`
и объясняют балл, сюда едет итог.

Причины хранятся текстом: веса скоринга выведены из замера на пяти
донорах и будут меняться, а вердикт, вынесенный старыми весами, обязан
остаться объяснимым после правки.

Решение человека лежит отдельно от вердикта скоринга — иначе не по чему
считать, как часто скоринг ошибается, а допуск по ложным рекламодателям
десять процентов.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c4a81f95"
down_revision: Union[str, Sequence[str], None] = "a4d7f1c92b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "advertiser_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("crawl_run_id", sa.Integer(), nullable=False),
        sa.Column("donor_host", sa.String(length=255), nullable=False),
        sa.Column("target_root", sa.String(length=255), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum("bought", "pending", "skipped", "blocked", name="verdict"),
            nullable=False,
        ),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("links", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("best_page_url", sa.Text(), nullable=True),
        sa.Column("best_anchor", sa.Text(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_candidates_run", "advertiser_candidates", ["crawl_run_id"], unique=False)
    op.create_index("idx_candidates_verdict", "advertiser_candidates", ["verdict"], unique=False)
    op.create_index(
        "idx_candidates_target_root", "advertiser_candidates", ["target_root"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_candidates_target_root", table_name="advertiser_candidates")
    op.drop_index("idx_candidates_verdict", table_name="advertiser_candidates")
    op.drop_index("idx_candidates_run", table_name="advertiser_candidates")
    op.drop_table("advertiser_candidates")
    sa.Enum(name="verdict").drop(op.get_bind(), checkfirst=True)
