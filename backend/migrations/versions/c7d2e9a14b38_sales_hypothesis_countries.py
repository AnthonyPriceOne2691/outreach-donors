"""поле гипотезы «страны компаний» и факты судьи сегмента в кэше вердиктов

Revision ID: c7d2e9a14b38
Revises: 1f5d08fe920b
Create Date: 2026-10-20 21:00:00.000000

Решения владельца 20.10.2026 к судье сегмента v4: какие страны бизнеса подходят гипотезе — поле
карточки гипотезы, а не правило судьи (`sales/hypothesis_countries.py`):

- `sales_hypotheses.other_countries` — «Компании из других стран»: `review` (решает человек; так у
  каждой гипотезы, и прежних тоже) или `accept`;
- `sales_hypotheses.excluded_countries` — «Не пишем компаниям из»: коды ISO 3166-1 alpha-2, пусто.

Судья v4 отдаёт факты сайта, полосу ставит код (`sales/segment_rules.py`); факты лежат в кэше
вердиктов — `sales_segment_verdicts.facts`, — и правка поля пересчитывает полосы без модели. У строк
прежних версий фактов нет: кэш их и так не отдаёт (другая версия промпта).

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN берёт ACCESS EXCLUSIVE, и за
долгой транзакцией прохода судьи выкатка встала бы, а за ней — все запросы к гипотезам; с пределом
она честно падает за 5 с, и её повторяют. Умолчания — постоянные: таблица не переписывается.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d2e9a14b38"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "1f5d08fe920b"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Поле «страны компаний» у гипотезы; факты сайта у вердикта судьи."""
    op.execute(LOCK_TIMEOUT)
    op.add_column(
        "sales_hypotheses",
        sa.Column("other_countries", sa.String(length=16), server_default="review", nullable=False),
    )
    op.add_column(
        "sales_hypotheses",
        sa.Column(
            "excluded_countries",
            postgresql.ARRAY(sa.String(length=2)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "sales_segment_verdicts",
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Снять поле и факты: прежний судья их не знает — гипотезы снова без поля, кэш v4 без фактов
    не отдаётся, и следующий проход спросит модель снова."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("sales_segment_verdicts", "facts")
    op.drop_column("sales_hypotheses", "excluded_countries")
    op.drop_column("sales_hypotheses", "other_countries")
