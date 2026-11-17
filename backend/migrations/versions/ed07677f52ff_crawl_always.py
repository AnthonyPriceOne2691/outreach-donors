"""обход 24/7: выключатель строками истории, глубина донора, номер части у обхода, сигнал «пройдено всё»

Revision ID: ed07677f52ff
Revises: 43f1b16b7317
Create Date: 2026-11-17 18:00:00.000000

Обход доноров идёт постоянно, частями (`crawl/always.py`): сначала сайты, которых не обходили, потом
по 10 000 страниц каждого, дальше — ещё по 10 000, сначала доходным, — если его запустили.

- `crawl_always_switches` — идёт ли обход 24/7, порог «мало рекламодателей» и «переобход слабых»:
  строка на каждую смену, действует последняя, строк нет — на паузе. Кто и когда — в строке.
- `crawl_depths` — сколько прошла цепочка частей по донору, откуда её продолжить (сжатый чекпоинт),
  доля продающих страниц, сколько старых частей судья не читал (потолок ссылок в памяти) и пометка
  «мало рекламодателей» руками — строка на донора.
- `crawl_runs.depth_part` — номер части у обхода цепочки; у остальных обходов пусто.
- `crawl_always_signals` — «пройдено всё», сказанное в Telegram: уникальный номер эпохи держит
  «один раз», пока не появится новая работа.
- значения `crawl_always_changed` журнала и `paused` причины конца обхода. В этой же ревизии ими не
  пользоваться: в транзакции, где значение добавлено, Postgres этого не даёт.

Таблицы новые и пустые, колонка — пустая у всех строк: до первого запуска не меняется ничего. Замок
на `crawl_runs` короткий: колонка без умолчания добавляется без переписи таблицы. Откат снимает
таблицы и колонку: обход 24/7 останавливается, его законченные части остаются обычными обходами.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ed07677f52ff"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "43f1b16b7317"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SWITCHES = "crawl_always_switches"
DEPTHS = "crawl_depths"
SIGNALS = "crawl_always_signals"


def _stamped(name: str) -> sa.Column[Any]:
    """Метка строки — обязательная, с минутой записи, как у `TimestampedMixin` и `SetByMixin`."""
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _when(name: str) -> sa.Column[Any]:
    """Минута события — пустая, пока до него не дошло."""
    return sa.Column(name, sa.DateTime(timezone=True))


def _who(name: str) -> sa.Column[Any]:
    """Учётка, сделавшая смену: удалили — строка истории остаётся без неё."""
    return sa.Column(name, sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"))


def upgrade() -> None:
    """Таблицы выключателя, глубины доноров и сигнала, номер части у обхода, значения перечислений."""
    op.create_table(
        SWITCHES,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("recrawl_weak", sa.Boolean(), nullable=False),
        _who("set_by"),
        _stamped("set_at"),
    )
    op.create_table(
        DEPTHS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(255), unique=True, nullable=False),
        sa.Column("pages", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checkpoint", sa.LargeBinary()),
        sa.Column("selling_pages", sa.Integer()),
        sa.Column("yield_pct", sa.Float()),
        sa.Column("frozen_parts", sa.Integer(), server_default="0", nullable=False),
        _when("first_done_at"),
        _when("exhausted_at"),
        sa.Column("weak_override", sa.Boolean()),
        _who("weak_set_by"),
        _when("weak_set_at"),
        sa.Column(
            "last_run_id", sa.Integer(), sa.ForeignKey("crawl_runs.id", ondelete="SET NULL")
        ),
        _stamped("created_at"),
        _stamped("updated_at"),
    )
    op.create_table(
        SIGNALS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("epoch", sa.Integer(), unique=True, nullable=False),
        _stamped("sent_at"),
        _when("reopened_at"),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.add_column("crawl_runs", sa.Column("depth_part", sa.Integer(), nullable=True))
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'crawl_always_changed'")
    op.execute("ALTER TYPE stopreason ADD VALUE IF NOT EXISTS 'paused'")


def downgrade() -> None:
    """Таблиц и колонки нет — обходу 24/7 не на чем идти. Значения перечислений не снимаются:
    Postgres убирает их только пересозданием типа, а записям они не мешают."""
    op.drop_column("crawl_runs", "depth_part")
    op.drop_table(SIGNALS)
    op.drop_table(DEPTHS)
    op.drop_table(SWITCHES)
