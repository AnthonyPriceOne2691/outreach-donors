"""исход контакта: учётку провайдера закрыли

Revision ID: a4c7e91b58d3
Revises: d3f6b2a9c418
Create Date: 2026-09-22 12:40:00.000000

Замер 22.09.2026 поймал живой отказ Hunter: `restricted_account` приезжает
с кодом 429, то есть неотличим от «слишком часто», если смотреть на число.
Лестница писала таким доменам `rate_limited` — «предел запросов, повторим
позже», — и повторяла бы ступень вечно, потому что повтор здесь не лечит
ничего. Квота при этом цела: провайдер отвечает 200 на запрос остатка
и показывает полный запас.

«Кончились запросы» и «нас закрыли» ведут человека к разным действиям,
поэтому у второго свой исход, а не оттенок первого.
"""

from typing import Sequence, Union

from alembic import op

revision: str = 'a4c7e91b58d3'
down_revision: Union[str, Sequence[str], None] = 'd3f6b2a9c418'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE contactstatus ADD VALUE IF NOT EXISTS 'blocked'")


def downgrade() -> None:
    """Downgrade schema.

    Значение из перечисления Postgres не убирается: удалить его можно
    только пересозданием типа со всеми зависимостями, а это блокировка
    таблицы доноров на живой базе. Лишнее значение не мешает ничему.
    """
