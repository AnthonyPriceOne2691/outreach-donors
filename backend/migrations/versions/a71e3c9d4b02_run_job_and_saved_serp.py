"""прогон знает свою задачу и хранит оплаченную выдачу

Revision ID: a71e3c9d4b02
Revises: c93a17d5f204
Create Date: 2026-09-21 18:10:00.000000

Три изменения одной причины: прогон, поставленный в очередь, до сих пор
не существовал в базе, пока задача не доходила до первого платного
запроса. Между нажатием и этим моментом сервис не показывал ничего —
а если задачу не брал никто (воркер умер или его не подняли), то
и никогда.

    status=queued  — строка появляется по нажатию, а не по первой трате
    job_id         — по нему спрашивают Redis, жива ли задача
    depth_pages    — чтобы задаче хватало одного номера прогона
    candidates     — выдача, за которую заплатили, переживает смерть
                     воркера: продолжение не покупает её второй раз
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a71e3c9d4b02"
down_revision: Union[str, Sequence[str], None] = "c93a17d5f204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'queued'")
    op.add_column("runs", sa.Column("job_id", sa.String(length=64), nullable=True))
    # Умолчание нужно только на время заливки: у прошлых прогонов
    # глубины нет, а колонка обязательная. Дальше его снимаем — иначе
    # схема расходится с моделью, где умолчание живёт в питоне, и
    # сверка «миграции = модели» на этом справедливо падает.
    op.add_column(
        "runs",
        sa.Column("depth_pages", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("runs", "depth_pages", server_default=None)
    op.add_column("runs", sa.Column("candidates", postgresql.JSONB(), nullable=True))
    # Реапер ищет прогоны по «давно не обновлялся», и без индекса это
    # чтение всей таблицы каждые полминуты.
    op.create_index("idx_runs_updated_at", "runs", ["updated_at"])


def downgrade() -> None:
    """Downgrade schema.

    Значение перечисления Postgres не убирается: удалить его можно только
    пересозданием типа со всеми зависимостями, а это блокировка таблицы
    прогонов на живой базе. Лишнее значение ничему не мешает.
    """
    op.drop_index("idx_runs_updated_at", table_name="runs")
    op.drop_column("runs", "candidates")
    op.drop_column("runs", "depth_pages")
    op.drop_column("runs", "job_id")
