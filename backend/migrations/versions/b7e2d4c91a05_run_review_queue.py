"""прогон кончается очередью на рассмотрение

Revision ID: b7e2d4c91a05
Revises: a3f19c6d2e57
Create Date: 2026-09-23 23:50:00.000000

Пороги отвечают «годен ли по цифрам», человек — «берём ли». Прогон
23.09.2026 признал годными microsoft.com и x.com: цифры у брендов
отличные по построению. Кандидаты прогона — история решений по прогонам,
`donors.review` — последнее решение по домену.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d4c91a05"
down_revision: Union[str, Sequence[str], None] = "a3f19c6d2e57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "run_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "domain_id", sa.Integer(), sa.ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("carried", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "domain_id", name="uq_run_candidates_run_domain"),
    )
    op.create_index("idx_run_candidates_run_status", "run_candidates", ["run_id", "status"])
    op.create_index("idx_run_candidates_domain", "run_candidates", ["domain_id"])

    op.add_column("donors", sa.Column("review", sa.String(16), nullable=True))
    op.add_column("donors", sa.Column("review_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("donors", sa.Column("review_by", sa.String(255), nullable=True))
    op.create_index("idx_donors_review", "donors", ["review"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_donors_review", table_name="donors")
    op.drop_column("donors", "review_by")
    op.drop_column("donors", "review_at")
    op.drop_column("donors", "review")
    op.drop_index("idx_run_candidates_domain", table_name="run_candidates")
    op.drop_index("idx_run_candidates_run_status", table_name="run_candidates")
    op.drop_table("run_candidates")
