"""исход поиска адреса: платный поиск недоступен — не спросили

Revision ID: 1f5d08fe920b
Revises: a7c3e9f15b20
Create Date: 2026-10-20 20:00:00.000000

Проход, которому платную ступень выключили (`no_paid`: очередь сбора базы продаж без
своего флага, консольные `--no-paid`), отдавал сайту без адреса на страницах «адреса
нет» — как если бы платный сервис спросили и он не знал. Теперь у такого исхода своё
значение `paid_off`: общая очередь — доноры и рекламодатели — берёт эти домены снова через
сутки, если платный поиск включён, иначе держит их, как «адреса нет» (`contacts/attempts.py`);
очередь сбора продаж решает сама. Тот же исход — у непринятого ключа и закрытой учётки
платного сервиса (до этого — `blocked`, `contacts/provider_step.py`).

**Значение добавляется вне транзакции выкатки** (`autocommit_block`): Postgres не даёт
пользоваться новым значением перечисления в той транзакции, где оно добавлено, а
выкатка идёт одной транзакцией на все ревизии — следующая ревизия, записавшая
`paid_off`, упала бы. `IF NOT EXISTS` делает повтор безвредным.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "1f5d08fe920b"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "a7c3e9f15b20"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Таблицы с исходом поиска адреса (`ContactAttemptMixin`): доноры, рекламодатели,
#: кандидаты сбора базы продаж.
_QUEUES = ("donors", "advertisers", "sales_segment_candidates")


def upgrade() -> None:
    """Значение `paid_off` у типа исхода поиска адреса."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE contactstatus ADD VALUE IF NOT EXISTS 'paid_off'")


def downgrade() -> None:
    """Исход `paid_off` — обратно в `not_found`; значение у типа остаётся.

    Снять значение перечисления Postgres не умеет — только пересоздать тип со всеми
    колонками, которые на него ссылаются (как у `blocked`, a4c7e91b58d3, и `no_answer`,
    b3e8d1f04a62). Оставшееся значение прежнему коду не мешает, а строка с ним — мешает:
    прежний код не прочтёт её и ответит 500. Поэтому откат переводит такие строки в то,
    что записал бы прежний код, — «адреса нет». Что именно было выключенным платным
    поиском, после отката не отличить; домены вернутся в очередь своим сроком.
    """
    for table in _QUEUES:
        op.execute(
            f"UPDATE {table} SET contact_status = 'not_found' WHERE contact_status = 'paid_off'"
        )
