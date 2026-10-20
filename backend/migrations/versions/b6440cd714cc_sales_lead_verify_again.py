"""перепроверка адреса лида по сроку, пока Hunter «ещё проверяет»

Revision ID: b6440cd714cc
Revises: b14a5e5368a1
Create Date: 2026-10-20 11:06:45.000000

Разбор прогона сбора 20.10: Hunter отвечал «ещё проверяет» (202) и после переспросов прохода, и
лид оставался «новым» со словами «повторим следующей очисткой» — следующей очистки никто не
запускал, лиды висели неделю. Теперь перепроверка идёт сама (`sales/verify_again.py`): у лида —
срок следующей перепроверки и момент первого «ещё проверяет», от которого считается окно; после
окна — «не проверить» словами. Вердикт лида колонки не трогают: его держат правила первого
письма и второго `unknown`. Частичный индекс — задача перепроверки ищет лидов по сроку.

Колонки новые: у прежних лидов срока нет — они ведут себя как раньше.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN берёт ACCESS EXCLUSIVE на
`sales_leads`, а индекс без CONCURRENTLY держит запись всё время сборки. За долгой транзакцией
очистки или сбора выкатка встала бы, а за ней — все запросы к лидам; с пределом она честно падает
за 5 с, и её повторяют.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6440cd714cc"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b14a5e5368a1"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "idx_sales_leads_verify_again"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    """Начало «ещё проверяет» подряд и срок перепроверки у лида продаж; индекс по сроку."""
    op.execute(LOCK_TIMEOUT)
    op.add_column(
        "sales_leads", sa.Column("verify_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sales_leads", sa.Column("verify_again_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        INDEX,
        "sales_leads",
        ["verify_again_at"],
        postgresql_where=sa.text("verify_again_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Снять срок и окно: отложенная перепроверка прежнему коду неизвестна — такие лиды остаются
    «новыми» со словами и ждут очистки, как до неё."""
    op.execute(LOCK_TIMEOUT)
    op.drop_index(INDEX, table_name="sales_leads")
    op.drop_column("sales_leads", "verify_again_at")
    op.drop_column("sales_leads", "verify_since")
