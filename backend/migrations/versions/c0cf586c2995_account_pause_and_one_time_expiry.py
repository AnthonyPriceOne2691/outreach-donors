"""учётки: срок разового пароля и пауза входа после неудач подряд

Revision ID: c0cf586c2995
Revises: b7e4c2a9d1f3
Create Date: 2026-11-20 12:00:00.000000

Аудит 20.11.2026, учётки и пароли.

- `users.password_set_at` — когда пароль выдан или сменён. Разовый пароль от админа
  (`must_change_password`) старше `ACCESS_ONE_TIME_TTL_HOURS` больше не пускает: пароль из
  переписки не остаётся ключом навсегда. Уже заведённым — минута миграции: выкатка не запирает
  того, у кого разовый на руках, срок ему идёт с неё.
- `users.failed_logins` — неудач входа подряд, `users.locked_until` — до какой минуты вход учётки
  на паузе (`access/lockout.py`). В базе, а не в памяти процесса: выкатка не обнуляет счёт подбора.
  У всех строк — ноль и пусто: паузы ни у кого нет.
- значение `login_paused` журнала — начало паузы. В этой же ревизии им не пользоваться: в
  транзакции, где значение добавлено, Postgres этого не даёт.

Замок на `users` короткий: умолчания постоянные (`now()` считается один раз на команду), и
колонки добавляются без переписи таблицы. Откат снимает колонки — срока у разового и паузы
входа больше нет; значение перечисления остаётся: Postgres убирает его только пересозданием
типа, а записям оно не мешает.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c0cf586c2995"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b7e4c2a9d1f3"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Срок разового пароля, счёт неудач и пауза входа, значение журнала."""
    op.add_column(
        "users",
        sa.Column(
            "password_set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'login_paused'")


def downgrade() -> None:
    """Колонок нет — разовый пароль без срока, пауз входа нет; значение журнала остаётся."""
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_logins")
    op.drop_column("users", "password_set_at")
