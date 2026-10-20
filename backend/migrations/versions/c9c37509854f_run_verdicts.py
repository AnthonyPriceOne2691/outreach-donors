"""вердикт домена — у прогона: страной и порогами прогона

Revision ID: c9c37509854f
Revises: 2b9c4561c381
Create Date: 2026-10-20 15:00:00.000000

Аудит 20.10.2026: вердикт региона и порогов лежал на домене (`donors.status`) без страны
и порогов, для которых его вынесли, а свежий домен прогон не перепроверял 90 дней —
годный для США предлагался в прогоне Канады. Теперь прогон судит каждый свой домен сам
и пишет вердикт строкой на пару «прогон + домен»: по ней он кладёт годных в очередь.

Таблица новая и пустая: у прогонов до неё строк нет, и у них всё как было — очередь
уже лежит, отчёт записан. Консоль `outreach review-queue` судит такой прогон заново по
сохранённым метрикам.

У донора — два вердикта: для выходов (`status`, как был) и последнего замера
(`measured_status`, `measured_reason`). Принятому человеком первый понижает только вердикт
прогона, где приняли, а снятое решение возвращает второй. До этой ревизии замер писал
`status` сам, и у каждого замеренного донора он и есть вердикт замера — им колонки
и заполняются; у заведённого руками и липового замера не было, у них пусто.

Откат снимает таблицу и колонки: прежний код их не читает.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN берёт ACCESS EXCLUSIVE на
`donors`, а заполнение колонок держит его до конца ревизии. За долгой транзакцией обхода или
поиска адресов выкатка встала бы, а за ней — все запросы к донорам; с пределом она честно падает
за 5 с, и её повторяют (как в ed31d48a1ec2 и b6440cd714cc).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9c37509854f"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "2b9c4561c381"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def _stamps() -> list[sa.Column]:
    """Метки времени строки, как у моделей с `TimestampedMixin`."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    """Вердикты прогонов: строка на пару «прогон + домен»; у донора — вердикт замера."""
    op.execute(LOCK_TIMEOUT)
    op.create_table(
        "run_verdicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cause", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("measured", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_stamps(),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "domain_id", name="uq_run_verdicts_run_domain"),
    )
    op.create_index("idx_run_verdicts_run_status", "run_verdicts", ["run_id", "status"])
    op.create_index("idx_run_verdicts_domain", "run_verdicts", ["domain_id"])
    op.add_column("donors", sa.Column("measured_status", sa.String(length=16), nullable=True))
    op.add_column("donors", sa.Column("measured_reason", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE donors SET measured_status = status::text, measured_reason = reject_reason "
        "WHERE metrics_refreshed_at IS NOT NULL"
    )


def downgrade() -> None:
    """Снять вердикты прогонов и вердикт замера: прежний код судит по `donors.status`."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("donors", "measured_reason")
    op.drop_column("donors", "measured_status")
    op.drop_index("idx_run_verdicts_domain", table_name="run_verdicts")
    op.drop_index("idx_run_verdicts_run_status", table_name="run_verdicts")
    op.drop_table("run_verdicts")
