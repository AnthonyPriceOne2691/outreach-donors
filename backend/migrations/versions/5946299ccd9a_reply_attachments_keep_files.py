"""вложения ответов — отдельной таблицей и вместе с файлами

Revision ID: 5946299ccd9a
Revises: b7d3e9a15c42
Create Date: 2026-09-28 12:00:00.000000

До этой миграции у ответа хранился только список вложений (`replies.attachments`,
JSON: имя, тип, «принято»), а сами файлы маршрут приёма выбрасывал. Платформа
приёма — единственный получатель письма, другой копии нет: прайс, присланный
донором файлом, терялся навсегда, и открыть его человеку было нечем.

Теперь каждое вложение — строка `reply_attachments` с самим файлом, а у
файла, который не сохранён (опасное расширение, сверх числа или размера), —
причина словами.

**Старый список переезжает в таблицу, колонка уходит.** Два места для одного
и того же «что пришло файлами» разошлись бы при первой правке одного из них,
и экран показывал бы то одно, то другое. Файлов у старых вложений нет
(их не сохраняли), и причина так и сказана; размер у них был всегда ноль
(платформа его не называет) — он переезжает как «неизвестен», а не как ноль.

Откат собирает список обратно из таблицы, но **файлы при откате теряются**:
в JSON им места нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5946299ccd9a"
down_revision: Union[str, Sequence[str], None] = "b7d3e9a15c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Старый список → строки таблицы. Порядок вложений сохраняется
#: (`WITH ORDINALITY`), испорченный элемент списка пропускается, а не роняет
#: миграцию на живой базе.
_MOVE_OLD_LIST = """
INSERT INTO reply_attachments (reply_id, name, content_type, size, data, accepted, reason, created_at)
SELECT
    r.id,
    left(coalesce(nullif(item->>'имя', ''), 'без имени'), 255),
    left(item->>'тип', 255),
    nullif(
        CASE WHEN jsonb_typeof(item->'байт') = 'number'
             THEN (item->>'байт')::numeric::integer END,
        0
    ),
    NULL,
    false,
    CASE
        WHEN jsonb_typeof(item->'принято') = 'boolean' AND (item->>'принято')::boolean
        THEN 'ответ принят до того, как сервис начал хранить вложения'
        ELSE 'исполняемый файл или скрипт, такие не принимаются'
    END,
    r.created_at
FROM (
    SELECT id, created_at, attachments FROM replies
    WHERE jsonb_typeof(attachments) = 'array'
) AS r
CROSS JOIN LATERAL jsonb_array_elements(r.attachments) WITH ORDINALITY AS listed(item, position)
WHERE jsonb_typeof(item) = 'object'
ORDER BY r.id, listed.position
"""

#: Таблица → старый список. Файлы в него не помещаются и теряются.
_RESTORE_OLD_LIST = """
UPDATE replies AS r
SET attachments = listed.items
FROM (
    SELECT
        reply_id,
        jsonb_agg(
            jsonb_build_object(
                'имя', name, 'байт', coalesce(size, 0), 'тип', content_type, 'принято', accepted
            )
            ORDER BY id
        ) AS items
    FROM reply_attachments
    GROUP BY reply_id
) AS listed
WHERE listed.reply_id = r.id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "reply_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reply_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["reply_id"], ["replies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_reply_attachments_reply_id", "reply_attachments", ["reply_id"], unique=False
    )
    op.execute(_MOVE_OLD_LIST)
    op.drop_column("replies", "attachments")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("replies", sa.Column("attachments", postgresql.JSONB(), nullable=True))
    op.execute(_RESTORE_OLD_LIST)
    op.drop_index("idx_reply_attachments_reply_id", table_name="reply_attachments")
    op.drop_table("reply_attachments")
