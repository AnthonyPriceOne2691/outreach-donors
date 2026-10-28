"""память донора: решение человека и спрошенный DR по паре «донор + домен» переживают повтор обхода

Revision ID: 77ff8a338610
Revises: 7b642c7865f2
Create Date: 2026-10-28 12:00:00.000000

Решение владельца 28.10.2026: «Переобойти» — у последнего обхода каждого донора, и повтор стал
обычным делом. Кандидаты донора — по его последнему обходу (`crawl/gate.py`): новый заменяет
строки прежнего и переносил решение человека и спрошенный DR только доменам, которые нашёл
снова. Повтор без домена — окно карты сдвинулось к новым статьям, потолок срезал старые, повтор
кончился частично — стирал их вместе со строкой. Домен, найденный следующим повтором,
возвращался без решения: «куплено» снова заводило рекламодателя, которому сказали «не пишем»,
а DR покупался второй раз.

Теперь решение и DR живут и в памяти донора (`candidate_memory`): строка на пару «донор +
домен», колонки — те же, что у кандидата. Пересчёт пишет её до того, как стереть строки
кандидатов, а читает раньше них.

Таблица новая и пустая: первый пересчёт после выкатки положит в неё всё, что лежит у нынешних
строк кандидатов. Решения, которые повтор стёр до неё, она не вернёт. Существующих таблиц
ревизия не трогает — замок не нужен. Откат снимает таблицу: прежний код её не читает, а решения
доменов, которых последний обход не нашёл, уходят вместе с ней — как стирал их и прежний код.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "77ff8a338610"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "7b642c7865f2"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "candidate_memory"


def upgrade() -> None:
    """Память донора: решение и DR по паре «донор + домен», пара уникальна."""
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("donor_host", sa.String(255), nullable=False),
        sa.Column("target_root", sa.String(255), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dr", sa.Integer(), nullable=True),
        sa.Column("dr_checked_at", sa.DateTime(timezone=True), nullable=True),
        *(
            sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for name in ("created_at", "updated_at")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("donor_host", "target_root", name="uq_candidate_memory_pair"),
    )


def downgrade() -> None:
    """Снять память: прежний код переносит решения и DR только со строк кандидатов."""
    op.drop_table(TABLE)
