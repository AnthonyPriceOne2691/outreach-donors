"""ответы: решение человека — лид рекламодателя в отказе

Revision ID: e9cc40d23f04
Revises: ce69f6ea12a1
Create Date: 2026-11-25 13:00:00.000000

Слово владельца 25.11.2026: настоящие отказы рекламодателей висели в «ждёт человека», а снять их
можно было только «Взять в работу» — с передачей в CRM. «В отказ» снимает ответ с ожидания теми же
`reviewed_by`/`reviewed_at`, по которым «ждёт человека» считают все этапы; отличает отказ от взятого
`replies.decision` (`domain.ReplyDecision`): `refused`. У принятых до ревизии — пусто: все
они взяты в работу или разобраны.

Колонка без умолчания — Postgres добавляет её без перезаписи таблицы; **замок — с пределом ожидания**
(`SET LOCAL lock_timeout`), как у `ce69f6ea12a1`: за долгой транзакцией приёма выкатка встала бы
вместе с вебхуком ответов, а с пределом падает словами через пять секунд — повторить.

Событие журнала `lead_refused` — новое значение перечисления `auditaction`. Откат снимает колонку;
значение перечисления остаётся: убрать его можно только пересозданием типа с блокировкой журнала.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9cc40d23f04"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "ce69f6ea12a1"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Колонка решения у ответа и событие журнала «лид в отказ»."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("replies", sa.Column("decision", sa.String(length=16), nullable=True))
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'lead_refused'")


def downgrade() -> None:
    """Снять колонку решения; значение перечисления журнала остаётся."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("replies", "decision")
