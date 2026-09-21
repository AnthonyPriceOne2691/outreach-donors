"""приём ответов: идентификатор письма, отправитель, тема, вложения

Revision ID: 9a2f1c6b48d3
Revises: 8c1d5e4a72b0
Create Date: 2026-09-21 13:05:00.000000

Четыре колонки, и каждая закрывает свою дыру.

`inbound_message_id` — защита от повтора. Провайдер доставляет вебхуки
«хотя бы один раз» и повторяет их при сбое; без отметки повтор давал бы
второй ответ, второй разбор и второй платный вызов модели.

`from_email` — ответ приходит не с того адреса, которому писали, и это
норма: на общий ящик смотрит секретарь. Выводить отправителя из контакта
значит записать не того, кто ответил.

`subject` — по теме человек узнаёт письмо в списке, не открывая его.

`attachments` — прайс приходит файлом чаще, чем текстом. Сами файлы
здесь не лежат, лежит то, что о них известно: ответ, выглядящий пустым,
это ответ, из которого не видно главного.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9a2f1c6b48d3'
down_revision: Union[str, Sequence[str], None] = '8c1d5e4a72b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('replies', sa.Column('inbound_message_id', sa.String(length=255), nullable=True))
    op.add_column('replies', sa.Column('from_email', sa.String(length=255), nullable=True))
    op.add_column('replies', sa.Column('subject', sa.String(length=512), nullable=True))
    op.add_column('replies', sa.Column('attachments', postgresql.JSONB(), nullable=True))
    op.create_unique_constraint('uq_replies_inbound_message_id', 'replies', ['inbound_message_id'])
    # Ручная очередь разбора: уверенность ниже порога и разбор не подтверждён.
    op.create_index(
        'idx_replies_confidence_reviewed', 'replies', ['confidence', 'reviewed_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_replies_confidence_reviewed', table_name='replies')
    op.drop_constraint('uq_replies_inbound_message_id', 'replies', type_='unique')
    op.drop_column('replies', 'attachments')
    op.drop_column('replies', 'subject')
    op.drop_column('replies', 'from_email')
    op.drop_column('replies', 'inbound_message_id')
