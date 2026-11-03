"""суд модели о спорных кандидатах: факты с цитатами и совет таблицы правил

Revision ID: e3c9a5d71f20
Revises: d8a2f6c31e47
Create Date: 2026-11-03 15:00:00.000000

Ядро судьи рекламодателей (проект автоматизации спорных, PR 5): модель называет факты о ссылке и
о домене с дословными цитатами, совет «пишем», «не пишем» или «не понять» ставит код по таблице
правил (`crawl/judge_rules.py`). Строка на пару «донор + домен», отпечаток того, что видел судья, и
когорту фактов — без внешних ключей: пересчёт весов стирает и заводит строки кандидатов заново,
а суд остаётся (`core/models/advertiser.AdvertiserJudgementModel`).

Таблица новая и пустая: ядро без прохода её не пишет, проход — следующим PR. Существующих таблиц
ревизия не трогает — замок не нужен. Откат снимает таблицу вместе с судом: прежний код её не читает.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3c9a5d71f20"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "d8a2f6c31e47"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "advertiser_judgements"


def upgrade() -> None:
    """Суд о паре «донор + домен»: ключ — пара, отпечаток контекста и когорта фактов."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("donor_host", sa.String(255), nullable=False),
        sa.Column("target_root", sa.String(255), nullable=False),
        sa.Column("context_print", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("prompt_print", sa.String(16), nullable=False),
        sa.Column("examples_print", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("rules_version", sa.String(64), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("advice", sa.String(16), nullable=False),
        sa.Column("rule", sa.String(32), nullable=False),
        sa.Column("seen", sa.Text(), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("donor_niche", sa.String(32), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("retried", sa.Boolean(), nullable=False),
        *(
            sa.Column(
                name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
            )
            for name in ("created_at", "updated_at")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "donor_host",
            "target_root",
            "context_print",
            "prompt_version",
            "prompt_print",
            "examples_print",
            "model",
            name="uq_advertiser_judgements_key",
        ),
    )
    op.create_index("idx_advertiser_judgements_pair", TABLE, ["donor_host", "target_root"])


def downgrade() -> None:
    """Снять суд: прежний код его не читает."""
    op.drop_index("idx_advertiser_judgements_pair", table_name=TABLE)
    op.drop_table(TABLE)
