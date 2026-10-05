"""рекламодатели: бизнесы ниши из выдачи прогона — источник, прогон и решение человека

Revision ID: 27a07f0ca34b
Revises: a51e5688f79e
Create Date: 2026-10-05 00:40:00.000000

Решение Anthony 04.10.2026: сайт, который сам продаёт в нише прогона (судья:
«продаёт своё»), — не донор, а кандидат в рекламодатели. До этого
рекламодатели приходили только из обхода исходящих ссылок наших доноров, и
у строки не было ни источника, ни прогона, ни решения человека по ней самой.

- `source`: `links` — нашли по ссылке на нашем доноре (как было), `niche` —
  бизнес из выдачи прогона;
- `found_run_id`: прогон, в выдаче которого он нашёлся, — его ниша и страна
  нужны письму;
- `decided_at` / `decided_by`: решение человека «пишем / не пишем» по
  бизнесу ниши (у найденных по ссылке оно живёт у кандидата обхода).

Только добавление: у прежних строк источник `links`, остальное пусто.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "27a07f0ca34b"
down_revision: Union[str, Sequence[str], None] = "a51e5688f79e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Источник, прогон и решение человека у рекламодателя."""
    op.add_column(
        "advertisers",
        sa.Column("source", sa.String(length=16), server_default="links", nullable=False),
    )
    op.add_column("advertisers", sa.Column("found_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "advertisers_found_run_id_fkey",
        "advertisers",
        "runs",
        ["found_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "advertisers", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("advertisers", sa.Column("decided_by", sa.String(length=128), nullable=True))
    op.create_index("idx_advertisers_source", "advertisers", ["source"])


def downgrade() -> None:
    """Снять индекс, колонки и связь — в обратном порядке."""
    op.drop_index("idx_advertisers_source", table_name="advertisers")
    op.drop_column("advertisers", "decided_by")
    op.drop_column("advertisers", "decided_at")
    op.drop_constraint("advertisers_found_run_id_fkey", "advertisers", type_="foreignkey")
    op.drop_column("advertisers", "found_run_id")
    op.drop_column("advertisers", "source")
