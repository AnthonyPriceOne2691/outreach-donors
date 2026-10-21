"""наборы «Отправителя» продаж по языку письма

Revision ID: abcc1e4edf4a
Revises: c7d2e9a14b38
Create Date: 2026-10-21 18:30:00.000000

Английское письмо уходило с русскими подписью, физическим адресом и именем в From: у «Отправителя»
был один набор полей на все языки. Теперь у строки отправителя — наборы по языку письма
(`sales_settings.languages`): имя, должность, подпись и физический адрес на каждом языке цепочек;
пустое поле набора — общее поле (`sales/sender.py`). Сайт, Telegram и ссылка на созвон — общие.

**Колонка строки, а не своя таблица**: отправитель — одна строка, и экран пишет его целиком одной
правкой — с одним автором, временем правки и записью журнала (`sales/sender_columns.py`).

Колонка новая и пустая: у прежней строки наборов нет — письма подписаны общим набором, как раньше;
строка не переписывается (ADD COLUMN без умолчания). В миграции — только литералы: код приложения
меняется, а ревизия должна подниматься и откатываться такой, какой её записали.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN и DROP COLUMN берут ACCESS
EXCLUSIVE на `sales_settings`, а строку отправителя читает каждая отправка и сборка писем продаж. За
долгой транзакцией сборки выкатка встала бы, а за ней — вся отправка; с пределом она честно падает за
5 с, и её повторяют.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "abcc1e4edf4a"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c7d2e9a14b38"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Наборы по языку письма у строки отправителя продаж — пустые."""
    op.execute(LOCK_TIMEOUT)
    op.add_column(
        "sales_settings",
        sa.Column("languages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Снять наборы: прежний код их не знает — письма на всех языках снова подписаны общим
    набором. Заданные наборы теряются вместе с колонкой: общий набор цел."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column("sales_settings", "languages")
