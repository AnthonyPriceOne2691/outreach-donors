"""советы судьи применены решениями и партия откатана — события журнала

Revision ID: fa5cd4ff98c6
Revises: e3c9a5d71f20
Create Date: 2026-11-11 10:00:00.000000

Советы судьи рекламодателей применяются решениями по спорным парам порциями одной партии
(`crawl/judge_apply.py`), партия откатывается целиком (`crawl/judge_revert.py`). Решение ставит не
человек — в журнале должно быть видно, кто запустил партию, какой полосой, сколько пар она решила и
кто её откатил.

Только добавление значений, отдельной ревизией: в транзакции, где значение добавлено, Postgres не
даёт им пользоваться. Таблиц ревизия не трогает — замок не нужен.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "fa5cd4ff98c6"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e3c9a5d71f20"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Новые значения перечисления журнала (`core/domain.AuditAction`).
VALUES = ("advertiser_judge_applied", "advertiser_judge_reverted")


def upgrade() -> None:
    """Upgrade schema."""
    for value in VALUES:
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка журнала
    на живой базе. Лишнее значение не мешает ничему.
    """
