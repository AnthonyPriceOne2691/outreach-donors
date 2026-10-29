"""очередь, которая уходит сама: запуски автоотправки очереди доноров и рекламодателей

Revision ID: c4e1a7b20d93
Revises: 77ff8a338610
Create Date: 2026-10-29 12:00:00.000000

Замечание тест-группы 29.10.2026: «нужна автоматическая ежедневная отправка очереди, если её
запустил, без ручного захода каждый день». Пачка «Отправить очередь» выбирала дневной лимит
ящиков, а остальное ждало следующего нажатия. Теперь кнопка с отметкой «дальше — сама»
включает режим (`letters/autosend.py`), и проход процесса разбора шлёт остаток сам, пока очередь
не кончится или человек не остановит.

Строка на запуск: кто и когда включил, кто, когда и почему остановил, чем кончился последний
проход. Идущий запуск у этапа и аудитории один — частный уникальный индекс по строкам без
остановки. Таблица новая и пустая, существующих ревизия не трогает — замок не нужен. Откат
снимает таблицу: прежний код её не читает, а режим без неё не включить и не продолжить.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e1a7b20d93"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "77ff8a338610"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "queue_autosend"


def upgrade() -> None:
    """Запуски автоотправки очереди: идущий у этапа и аудитории — один."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "stage",
            postgresql.ENUM("donors", "advertisers", "sales", name="stage", create_type=False),
            nullable=False,
        ),
        sa.Column("audience", sa.String(16), nullable=False, server_default="links"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "started_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stopped_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("passed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("unsure_id", sa.Integer(), nullable=True),
        *(
            sa.Column(
                name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
            )
            for name in ("created_at", "updated_at")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_queue_autosend_running",
        TABLE,
        ["stage", "audience"],
        unique=True,
        postgresql_where=sa.text("stopped_at IS NULL"),
    )


def downgrade() -> None:
    """Снять запуски: прежний код их не читает, очередь уходит только по нажатию."""
    op.drop_index("uq_queue_autosend_running", table_name=TABLE)
    op.drop_table(TABLE)
