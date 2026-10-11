"""продажи: о каком ответе телемаркетолог получил сообщение — ключ «Лид написал снова»

Revision ID: 1045a2d42013
Revises: ee6c0a44cbd4
Create Date: 2026-10-12 01:30:00.000000

Новое письмо лида, уже переданного телемаркетологу, уходит той же передачей: примечанием к той
же сделке и сообщением телемаркетологу «Лид написал снова» (`sales/handoff_telegram.py`). Одно и
то же письмо дважды не уходит — ключ номер ответа. У Kommo ключ свой (`noted_reply_id`), а Kommo и
Telegram отказывают порознь: Kommo молчит — сообщение всё равно уходит, примечание ждёт повтора.
Поэтому сообщению — своя колонка: о каком ответе последнее личное сообщение (`told_reply_id`) и
было ли оно «Лид написал снова» (`told_again`: копия в группу повторяется тем же текстом).

У прежних передач, где сообщение уже доставлено, ключ — отмеченный в Kommo ответ: о прошлых
письмах «Лид написал снова» задним числом не уходит. Откат снимает обе колонки.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1045a2d42013"
down_revision: Union[str, Sequence[str], None] = "ee6c0a44cbd4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ключ сообщения о лиде и его вид; у доставленных — ответ, отмеченный в Kommo."""
    op.add_column("sales_handoffs", sa.Column("told_reply_id", sa.Integer(), nullable=True))
    op.add_column(
        "sales_handoffs",
        sa.Column("told_again", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        "UPDATE sales_handoffs SET told_reply_id = noted_reply_id "
        "WHERE notified_link IS NOT NULL"
    )


def downgrade() -> None:
    """Снять обе колонки."""
    op.drop_column("sales_handoffs", "told_again")
    op.drop_column("sales_handoffs", "told_reply_id")
