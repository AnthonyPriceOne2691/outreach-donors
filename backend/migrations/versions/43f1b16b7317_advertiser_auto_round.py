"""автокруг рекламодателей: выключатель строками истории и строка круга на дату

Revision ID: 43f1b16b7317
Revises: 69c8a46a8093
Create Date: 2026-11-17 12:00:00.000000

После ночного шага судьи человек жал три кнопки: «Перевести в рекламодатели», «Собрать очередь» и
«Отправить очередь» с отметкой «дальше — сама». Теперь это делает автокруг раз в сутки
(`crawl/auto_round.py`) — если его включили.

- `advertiser_auto_round_switches` — включён ли автокруг и сколько писем в сутки: строка на каждую
  смену, действует последняя, строк нет — выключен. Кто и когда — в строке, как у полос судьи.
- `advertiser_auto_rounds` — круг даты: уникальная дата держит «один раз в сутки», итог остаётся
  следом, по шагу в строке круг продолжает с места после выкатки.
- значение журнала `advertiser_auto_round_changed` — смена выключателя или потолка. В этой же
  ревизии им не пользоваться: в транзакции, где значение добавлено, Postgres этого не даёт.

Таблицы новые и пустые: до первого включения не меняется ничего. Существующих таблиц ревизия не
трогает — замок не нужен. Откат снимает обе таблицы: автокруг выключен, а переведённые, собранные
и отправленные им письма остаются, как после кнопок.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "43f1b16b7317"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "69c8a46a8093"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SWITCHES = "advertiser_auto_round_switches"
ROUNDS = "advertiser_auto_rounds"


def _moments(*names: str, stamp: bool = False) -> list[sa.Column[Any]]:
    """Колонки времени с поясом: метки строки (`stamp`) — обязательные, с минутой записи, как у
    `TimestampedMixin`; остальные — пустые, пока шаг до них не дошёл."""
    if stamp:
        return [
            sa.Column(
                name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            )
            for name in names
        ]
    return [sa.Column(name, sa.DateTime(timezone=True), nullable=True) for name in names]


def upgrade() -> None:
    """Таблицы выключателя и кругов, событие журнала о смене выключателя."""
    op.create_table(
        SWITCHES,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("letters", sa.Integer(), nullable=False),
        # Кто и когда — как у `SetByMixin`: учётку удалили — строка истории остаётся без неё.
        sa.Column("set_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_table(
        ROUNDS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), unique=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *_moments("started_at", "finished_at", "asked_at"),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("note", sa.Text()),
        *_moments("created_at", "updated_at", stamp=True),
    )
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'advertiser_auto_round_changed'")


def downgrade() -> None:
    """Таблиц нет — автокругу не на чем идти, и выключателя нет. Значение журнала не снимается:
    Postgres убирает его из перечисления только пересозданием типа, а записям оно не мешает."""
    op.drop_table(ROUNDS)
    op.drop_table(SWITCHES)
