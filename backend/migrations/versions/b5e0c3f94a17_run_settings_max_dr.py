"""настройки прогона: порог крупного сайта — DR выше него «не подходит», писем нет

Revision ID: b5e0c3f94a17
Revises: 7c3e9a1f5b24
Create Date: 2026-10-12 12:00:00.000000

Решение владельца 12.10.2026 по сверке требований: «все пороги заданы цифрами и вынесены
в настройки прогона». Граница «кому не пишем … домены DR > 80» с 11.10.2026 отсекала крупные
сайты на первой ступени замера и на всех выходах, где пишем, но жила числом в коде. Теперь
это порог версии (`run_settings.max_dr`): прогон судит им, экран «Пороги» его правит, новая
версия переносит.

Только добавление колонки: прежние версии получают 80 — ту границу, что действовала,
и вердикты, вынесенные с ними, объясняются так же. Откат снимает колонку; код до этой
ревизии её не читает.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e0c3f94a17"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "7c3e9a1f5b24"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Порог крупного сайта у каждой версии настроек; у прежних — 80."""
    op.add_column(
        "run_settings",
        sa.Column("max_dr", sa.Integer(), nullable=False, server_default="80"),
    )


def downgrade() -> None:
    """Снять колонку."""
    op.drop_column("run_settings", "max_dr")
