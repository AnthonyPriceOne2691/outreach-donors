"""цена донора целиком: белая и серая, оплата, срок, тип ссылки, ответ — и имя отправителя ответа

Revision ID: 7c3e9a1f5b24
Revises: 12b040073dcc
Create Date: 2026-10-12 02:00:00.000000

Сверка ТЗ 12.10.2026: «разбирает его на поля — белая цена, серая цена, способы оплаты —
и кладёт в ту же базу». На донора ложилась одна цена (`last_price*`), а серая цена и
оплата оставались только у ответа; срок публикации и тип ссылки модель возвращала,
а не сохранял никто. Теперь всё это лежит у ответа и у донора рядом с ценой — тем же
ответом или той же записью руками (`donors/price.put_price`), — и у донора номер
ответа, из которого цена: карточка ведёт в его переписку.

У ответа — ещё имя из «От кого»: перед моделью оно уходит меткой, как адрес
(требование: «адреса и имена заменяются метками»), а разбор идёт задачей очереди после
приёма, и без хранения имени маскировать было бы нечего.

Только добавление колонок, все пустые. Прежние цены не дописываются: чьим ответом
легла цена до этой ревизии, в базе не записано, и догадка по времени положила бы
в карточку оплату чужого письма. Новые поля заполнит следующий ответ или цена руками.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c3e9a1f5b24"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "12b040073dcc"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Поля цены у донора и у ответа, имя отправителя у ответа."""
    op.add_column("donors", sa.Column("last_price_white", sa.DECIMAL(10, 2), nullable=True))
    op.add_column("donors", sa.Column("last_price_grey", sa.DECIMAL(10, 2), nullable=True))
    op.add_column(
        "donors", sa.Column("last_payment_methods", postgresql.JSONB(), nullable=True)
    )
    op.add_column("donors", sa.Column("last_placement_days", sa.Integer(), nullable=True))
    op.add_column("donors", sa.Column("last_link_type", sa.String(length=16), nullable=True))
    op.add_column("donors", sa.Column("last_price_reply_id", sa.Integer(), nullable=True))
    op.add_column("replies", sa.Column("placement_days", sa.Integer(), nullable=True))
    op.add_column("replies", sa.Column("link_type", sa.String(length=16), nullable=True))
    op.add_column("replies", sa.Column("from_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Снять добавленные колонки."""
    op.drop_column("replies", "from_name")
    op.drop_column("replies", "link_type")
    op.drop_column("replies", "placement_days")
    op.drop_column("donors", "last_price_reply_id")
    op.drop_column("donors", "last_link_type")
    op.drop_column("donors", "last_placement_days")
    op.drop_column("donors", "last_payment_methods")
    op.drop_column("donors", "last_price_grey")
    op.drop_column("donors", "last_price_white")
