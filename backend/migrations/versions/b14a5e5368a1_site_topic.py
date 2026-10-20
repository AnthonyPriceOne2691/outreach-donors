"""тематика сайта — тема из общего списка у домена

Revision ID: b14a5e5368a1
Revises: dbce021e159c
Create Date: 2026-10-20 15:00:00.000000

Оператор попросил фильтровать и искать тематичные сайты. Тема — свойство сайта, поэтому
у домена, рядом с вердиктом судьи: код темы и второй темы из общего списка
(`features/topics/taxonomy.py`), короткая причина, модель, версия промпта, по чему
определена и когда. Колонки пустые: новые сайты получает прогон, уже записанные —
досуд (`outreach topic-backfill`). Код — строкой, а не перечислением базы: новая тема
заводится без миграции.
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b14a5e5368a1"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "dbce021e159c"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Колонки темы у домена — в порядке добавления; откат снимает их в обратном.
COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("site_topic", sa.String(length=32)),
    ("site_topic_secondary", sa.String(length=32)),
    ("topic_reason", sa.String(length=256)),
    ("topic_model", sa.String(length=64)),
    ("topic_version", sa.String(length=32)),
    ("topic_source", sa.String(length=16)),
    ("topic_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    """Колонки темы — пустыми, без значения по умолчанию: таблица не переписывается.

    Индекса нет намеренно: фильтр — «основная или вторая тема», ИЛИ по двум колонкам, и
    индекс по одной из них план запроса не выбирал (последовательный проход по замеру на
    200 000 доменов), а `CREATE INDEX` на выкатке держал бы запись в `domains`.
    """
    for name, kind in COLUMNS:
        op.add_column("domains", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    """Темы уходят вместе с колонками: досуд определит их заново."""
    for name, _ in reversed(COLUMNS):
        op.drop_column("domains", name)
