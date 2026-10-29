"""дневные потолки расхода на модель — на экране «Расход», с историей смен

Revision ID: fdaa566c5a26
Revises: 77ff8a338610
Create Date: 2026-10-29 12:00:00.000000

Слово Anthony 29.10.2026: «чтобы администратор мог поднимать расходы сам — на «Расходе», как
лимит ключа Ahrefs, и на модель такую же штуку». До этого три дневных потолка модели — общий
(`LLM_DAILY_TOKEN_CAP`) и свои у продаж и агента переписки — жили только в `.env`: сбор базы
продаж, чья смета не помещалась в остаток, стоял до сброса, а поднять потолок можно было
только правкой сервера.

Строка на каждую смену блока: общий потолок сервиса и доли частей от него — продаж и агента
переписки, аутрич — остаток (уточнение Anthony 29.10: «по образцу Ahrefs — лимит пространства на
весь сервис, у ключей свои»). Действует последняя строка; строк нет — потолки из `.env`
(`core/llm_caps.py`). Таблица новая и пустая — действует
то же, что и до миграции. Значение журнала `llm_cap_changed` в миграциях не использовать: ADD
VALUE здесь же, а новым значением Postgres не даёт пользоваться в той транзакции, где оно
добавлено. Существующих таблиц ревизия не трогает — замок не нужен.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fdaa566c5a26"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "77ff8a338610"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "llm_caps"


def upgrade() -> None:
    """Таблица смен блока потолков модели и событие журнала о смене."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("general_tokens", sa.BigInteger(), nullable=True),
        sa.Column("sales_share", sa.SmallInteger(), nullable=False),
        sa.Column("agent_share", sa.SmallInteger(), nullable=False),
        sa.Column("outreach_share", sa.SmallInteger(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "general_tokens IS NULL OR general_tokens > 0", name="ck_llm_caps_general"
        ),
        sa.CheckConstraint(
            "sales_share % 5 = 0 AND agent_share % 5 = 0 AND outreach_share % 5 = 0 "
            "AND sales_share >= 0 AND agent_share >= 0 AND outreach_share >= 0 "
            "AND sales_share + agent_share + outreach_share <= 100",
            name="ck_llm_caps_shares",
        ),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'llm_cap_changed'")


def downgrade() -> None:
    """Снять таблицу — потолки снова только из `.env`. Значение журнала остаётся.

    Из перечисления Postgres значение не убирается без пересоздания типа со всеми
    зависимостями — это блокировка журнала на живой базе, а лишнее значение ничему не мешает.
    """
    op.drop_table(TABLE)
