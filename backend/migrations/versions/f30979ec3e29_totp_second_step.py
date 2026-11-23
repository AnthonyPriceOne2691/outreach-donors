"""учётки: вход с кодом из приложения — секрет, включение, последний шаг, коды восстановления

Revision ID: f30979ec3e29
Revises: d4f8a2c61e93
Create Date: 2026-11-23 12:00:00.000000

Второй шаг входа: код из приложения-аутентификатора (TOTP, `access/second_step.py`).

- `users.totp_secret` — секрет кода, зашифрованный ключом `AUTH_TOTP_KEY` (Fernet). Пока
  `users.totp_enabled_at` пусто, секрет начат, но кодом не подтверждён; `users.totp_last_step` —
  последний принятый шаг времени: код того же окна второй раз не пускает.
- `users.token_generation` — поколение пропусков: включение и сброс кода прибавляют единицу, и
  пропуска, выданные до этого, гаснут, как после смены пароля. У всех строк — ноль: отпечаток
  пропуска при нуле прежний (`tokens.password_stamp`), и выкатка никого не выбрасывает.
- `user_recovery_codes` — десять разовых кодов восстановления учётки, только хешами (SHA-256 от
  80 случайных бит). Строка учётки ушла — ушли и коды (`ON DELETE CASCADE`); учётки и так не
  удаляются, а отключаются. Прав роли приложения отдельно не нужно: таблицу она получает теми же
  правами, что все (`ALTER DEFAULT PRIVILEGES` владельца, `shared/database/app_role.py`), — коды
  выпускаются, тратятся и удаляются, это не журнал.
- значения журнала: `totp_enabled`, `totp_disabled`, `totp_reset`, `recovery_code_used`. В этой
  же ревизии ими не пользоваться: в транзакции, где значение добавлено, Postgres этого не даёт.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): колонки `users` и ключ новой таблицы на
`users` ждут замка таблицы, а за долгой транзакцией выкатка встала бы вместе со всеми входами. С
пределом выкатка падает словами через пять секунд — повторить.

Откат снимает таблицу и колонки: вход с кодом пропадает у всех, пропуска с поколением больше нуля
гаснут (отпечаток снова считается без поколения) — после отката все входят заново паролем.
Значения перечисления остаются: Postgres убирает их только пересозданием типа, записям они не
мешают.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f30979ec3e29"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "d4f8a2c61e93"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
TABLE = "user_recovery_codes"
INDEX = "idx_recovery_codes_user"
JOURNAL_VALUES = ("totp_enabled", "totp_disabled", "totp_reset", "recovery_code_used")


def upgrade() -> None:
    """Колонки кода у учётки, таблица кодов восстановления, значения журнала."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column("token_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(INDEX, TABLE, ["user_id"])
    for value in JOURNAL_VALUES:
        op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """Входа с кодом нет: таблица и колонки сняты; значения журнала остаются."""
    op.execute(LOCK_TIMEOUT)
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_column("users", "token_generation")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret")
