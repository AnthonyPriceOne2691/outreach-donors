"""рассылки: аудитория Этапа 2 — по найденным ссылкам или бизнесам ниши

Revision ID: 9c4792694f6a
Revises: 5d2c8e1a9f47
Create Date: 2026-10-05 01:30:00.000000

Письмо бизнесу ниши из выдачи (`crawl/niche.py`) — другой оффер и другие
добивки: размещения, которое «мы видели», у него нет. Рассылка помнит, кому
она, и сборка с добивками берут по ней свои шаблоны.

Только добавление: у прежних рассылок аудитория `links`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c4792694f6a"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5d2c8e1a9f47"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Аудитория рассылки, `links` у прежних."""
    op.add_column(
        "campaigns",
        sa.Column("audience", sa.String(length=16), server_default="links", nullable=False),
    )


def downgrade() -> None:
    """Снять колонку аудитории."""
    op.drop_column("campaigns", "audience")
