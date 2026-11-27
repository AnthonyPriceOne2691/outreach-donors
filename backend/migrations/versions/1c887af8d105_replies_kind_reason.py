"""ответы: почему автоответ — правило разбора и заголовок автомата

Revision ID: 1c887af8d105
Revises: e9cc40d23f04
Create Date: 2026-11-27 12:30:00.000000

`replies.kind_reason` — почему ответ помечен автоответом (`replies/classify.Verdict.reason`):
название правила разбора, а у подписи автомата — и заголовок с его значением из нашего списка
(«автоответчик по заголовкам (Precedence: bulk)»). Заголовков письма в базе нет: спорный автоответ
(по тексту — человек, по подписи автомата — нет) проверить было нечем. У принятых до ревизии —
пусто: заголовков не осталось, и пересчёт причину подписи не восстановит.

Колонка без умолчания — Postgres добавляет её без перезаписи таблицы. **Замок — с пределом
ожидания** (`SET LOCAL lock_timeout`), как у `ce69f6ea12a1`: ALTER ждёт замка `replies`, а за
долгой транзакцией приёма выкатка встала бы вместе с вебхуком ответов. С пределом выкатка падает
словами через пять секунд — повторить.

Откат снимает колонку: причина автоответа снова только в журнале приёма.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1c887af8d105"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "e9cc40d23f04"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Колонка причины автоответа у ответа."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("replies", sa.Column("kind_reason", sa.String(length=120), nullable=True))


def downgrade() -> None:
    """Снять колонку причины автоответа."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("replies", "kind_reason")
