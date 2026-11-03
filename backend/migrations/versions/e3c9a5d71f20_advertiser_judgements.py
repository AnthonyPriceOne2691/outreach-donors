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
#: Ключ строки: пара, отпечаток контекста и когорта фактов.
KEY = (
    "donor_host",
    "target_root",
    "context_print",
    "prompt_version",
    "prompt_print",
    "examples_print",
    "model",
)
#: Строковые колонки ключа и совета: имя → длина, все обязательные.
NAMES = {
    "donor_host": 255,
    "target_root": 255,
    "context_print": 64,
    "prompt_version": 64,
    "prompt_print": 16,
    "examples_print": 16,
    "model": 64,
    "rules_version": 64,
    "advice": 16,
    "rule": 32,
}


def upgrade() -> None:
    """Суд о паре «донор + домен»: ключ — пара, отпечаток контекста и когорта фактов."""
    stamp = {"server_default": sa.func.now(), "nullable": False}
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        *(sa.Column(name, sa.String(length=size), nullable=False) for name, size in NAMES.items()),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seen", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16)),
        sa.Column("donor_niche", sa.String(length=32)),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("retried", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **stamp),
        sa.Column("updated_at", sa.DateTime(timezone=True), **stamp),
        # Пара — начало ключа: выборку по ней база обслуживает уникальным индексом, своего не нужно.
        sa.UniqueConstraint(*KEY, name="uq_advertiser_judgements_key"),
    )


def downgrade() -> None:
    """Снять суд: прежний код его не читает."""
    op.drop_table(TABLE)
