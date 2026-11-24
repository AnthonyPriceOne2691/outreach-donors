"""ответы: чем привязан — метка, цепочка, отправитель или не привязан

Revision ID: ce69f6ea12a1
Revises: f30979ec3e29
Create Date: 2026-11-24 16:20:00.000000

`replies.bound_by` — способ привязки ответа к нашему письму (`replies/binding.BindingWay`): `label`,
`headers`, `sender` или `none`. Решение приёма, как и `unbound_reason` рядом: доля привязок по
отправителю и по цепочке — число отчёта, а жило оно только в итоге приёма в журнале контейнера,
который стирает каждая выкатка (24.11.2026 — четыре раза за день). У принятых до ревизии — пусто:
способ тогда нигде не сохранялся, и пересчёт его не восстановит.

Колонка без умолчания — Postgres добавляет её без перезаписи таблицы. **Замок — с пределом
ожидания** (`SET LOCAL lock_timeout`): ALTER ждёт замка `replies`, а за долгой транзакцией приёма
выкатка встала бы вместе с вебхуком ответов. С пределом выкатка падает словами через пять
секунд — повторить.

Откат снимает колонку: способ привязки снова только в журнале.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ce69f6ea12a1"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "f30979ec3e29"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Колонка способа привязки у ответа."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("replies", sa.Column("bound_by", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Снять колонку способа привязки."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("replies", "bound_by")
