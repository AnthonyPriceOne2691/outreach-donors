"""исход контакта: сайт не ответил — повтор с пределом

Revision ID: b3e8d1f04a62
Revises: 7baf5c71fce2
Create Date: 2026-10-02 12:00:00.000000

Обрыв, таймаут, 5xx или 429 на сайте записывались как «адреса нет»
(`not_found`), и донор, недоступный в момент сбоя, ждал повтора 180 дней.
Теперь у такого прохода свой исход — `no_answer`, — и он повторяется по
сроку: следующий прогон, через день, через неделю; четвёртый раз без
ответа — `not_found` (решение Anthony 01.10.2026, `contacts/attempts.py`).

Счёт попыток и причина — колонки у каждой очереди поиска (доноры,
рекламодатели): одна схема на все очереди, `ContactAttemptMixin`.

`ADD VALUE` идёт в той же миграции, что и колонки: новое значение здесь
нигде не пишется, а использовать его в той же транзакции и нельзя.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3e8d1f04a62'
down_revision: Union[str, Sequence[str], None] = '7baf5c71fce2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_QUEUES = ("donors", "advertisers")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE contactstatus ADD VALUE IF NOT EXISTS 'no_answer'")
    for table in _QUEUES:
        op.add_column(
            table,
            sa.Column("contact_tries", sa.SmallInteger(), server_default="0", nullable=False),
        )
        op.add_column(table, sa.Column("contact_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    Значение `no_answer` из перечисления не убирается — по той же причине,
    что и `blocked` (a4c7e91b58d3): только пересозданием типа со всеми
    зависимостями. Доноры с этим исходом перед откатом надо перевести
    в `not_found`, иначе старый код не прочтёт строку.
    """
    for table in _QUEUES:
        op.drop_column(table, "contact_reason")
        op.drop_column(table, "contact_tries")
