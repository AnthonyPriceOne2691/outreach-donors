"""перепроверка адреса лида, у которого Hunter «ещё проверяет»

Revision ID: b6440cd714cc
Revises: dbce021e159c
Create Date: 2026-10-20 11:06:45.000000

Разбор прогона сбора 20.10: Hunter отвечал «ещё проверяет» (202) и после переспросов прохода, и
лид оставался «новым» со словами «повторим следующей очисткой» — следующей очистки никто не
запускал, лиды висели неделю. Теперь перепроверка идёт сама (`sales/verify_again.py`): лиду
нужны срок и счёт — когда спросить снова и сколько раз уже ответили «ещё проверяет»; после
предела — «не проверить» словами. Вердикт лида колонки не трогают: его держат правила первого
письма и второго `unknown`.

Колонки новые: у прежних лидов счёт ноль и срока нет — они ведут себя как раньше.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6440cd714cc"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "dbce021e159c"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Счёт «ещё проверяет» подряд и срок перепроверки у лида продаж."""
    op.add_column(
        "sales_leads",
        sa.Column("verify_tries", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "sales_leads", sa.Column("verify_again_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Снять срок и счёт: отложенная перепроверка прежнему коду неизвестна — такие лиды остаются
    «новыми» со словами и ждут очистки, как до неё."""
    op.drop_column("sales_leads", "verify_again_at")
    op.drop_column("sales_leads", "verify_tries")
