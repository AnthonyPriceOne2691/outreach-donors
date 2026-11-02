"""потолок проверок адреса на экране «Расход» и снимок учётки Hunter

Revision ID: 3b7e1c94d2a6
Revises: c5d30109d388
Create Date: 2026-11-02 13:00:00.000000

Слово Anthony 02.11.2026: «заведи поле в настройки админа, как с Ahrefs и моделью, чтобы можно было
руками править», и на экране — общий остаток по учётке Hunter. До этого месячный потолок проверок
адреса жил только в `SALES_VERIFIER_MONTH_CAP`, а остаток учётки экран не показывал вовсе.

`hunter_caps` — строка на каждую смену, действует последняя; строк нет — потолок из `.env`
(`core/hunter_cap.py`). Снять настройку нельзя: значение не пустое, ноль — проверки остановлены.
`hunter_account_snapshot` — одна строка, последний ответ Hunter (`contacts/hunter_account.py`).
Обе таблицы новые и пустые: действует то же, что и до миграции, значение потолка ревизия не сеет.
Существующих таблиц ревизия не трогает — замок не нужен. Значение журнала `hunter_cap_changed`
в миграциях не использовать: ADD VALUE здесь же, а новым значением Postgres не даёт пользоваться
в той транзакции, где оно добавлено.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3b7e1c94d2a6"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c5d30109d388"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CAPS = "hunter_caps"
SNAPSHOT = "hunter_account_snapshot"

#: Счётчик учётки дробный: проверка адреса стоит половину кредита.
COUNTER = sa.DECIMAL(14, 2)


def _counter(prefix: str, *, nullable: bool) -> list[sa.Column]:
    """Колонки счётчика: выбрано, всего и осталось."""
    return [
        sa.Column(f"{prefix}_{part}", COUNTER, nullable=nullable)
        for part in ("used", "available", "remaining")
    ]


def _stamp(name: str) -> sa.Column[Any]:
    """Отметка времени строки: ставит база."""
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Таблица смен потолка, снимок учётки и событие журнала о смене."""
    op.create_table(
        CAPS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("verify_cap", sa.Integer(), nullable=False),
        sa.Column("set_by", sa.Integer(), nullable=True),
        _stamp("set_at"),
        sa.CheckConstraint("verify_cap >= 0", name="ck_hunter_caps_not_negative"),
        sa.ForeignKeyConstraint(["set_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        SNAPSHOT,
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("plan", sa.String(length=64), nullable=False),
        sa.Column("reset_on", sa.Date(), nullable=False),
        *_counter("credits", nullable=True),
        *_counter("searches", nullable=False),
        *_counter("verifications", nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_hunter_account_snapshot_single"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'hunter_cap_changed'")


def downgrade() -> None:
    """Снять таблицы — потолок снова только из `.env`. Значение журнала остаётся.

    Из перечисления Postgres значение не убирается без пересоздания типа со всеми
    зависимостями — это блокировка журнала на живой базе, а лишнее значение ничему не мешает.
    """
    op.drop_table(SNAPSHOT)
    op.drop_table(CAPS)
