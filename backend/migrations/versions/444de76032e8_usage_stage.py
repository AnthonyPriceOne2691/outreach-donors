"""расход: этап у строки журнала и два провайдера обхода Этапа 2

Revision ID: 444de76032e8
Revises: b5e0c3f94a17
Create Date: 2026-10-12 01:00:00.000000

Расход Этапа 2 на обход и прокси считается отдельно от Этапа 1 и держится своим
потолком: иначе один донор за защитой съест месяц. До этой ревизии у строки
расхода этапа не было: DR кандидатов Этапа 2 писался той же операцией `batch_metrics`,
что и прогон Этапа 1, и на экране «Расход» обе траты были одной строкой.

Три части:

1. колонка `usage_records.stage` — тип `stage`, пусто можно: пусто — трата общая
   на все этапы (отправка письма) или этап не назван (`core/usage.OPERATION_STAGES`);
2. значения `proxy` и `render` у `usageprovider` — платные уровни обхода
   (`crawl/spend.py`); в этой же ревизии они не используются;
3. разметка прошлых строк — только у операций, у которых этап один: выдача, судья
   площадок, ключи и трафик по странам — Этап 1; операции продаж — продажи. Метрики
   доменов (`batch_metrics`) не размечаются: их писали и прогон, и DR кандидатов,
   а номер прогона у строки стирает удаление прогона (`prune`) — по нему этап не узнать.

Продажи размечаются приведением текста, а не строкой `'sales'`: на чистой базе цепочка
идёт одной транзакцией, и значение `sales`, добавленное раньше в ней же (39e342cb2b21),
строкой в запросе Postgres не принимает вовсе («unsafe use of new value»), даже при
пустой таблице. Приведение исполняется по строке — строк нет, приводить нечего.

Откат снимает колонку; значения перечисления остаются — как у `hunter` (2d9877260a6d).

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`). Добавить и снять колонку —
исключительный замок таблицы расхода, а в неё пишут обходчики, прогоны и письма. Долгая
чужая транзакция без предела держала бы выкатку, а за выкаткой — каждого, кто пишет
расход. С пределом выкатка падает словами через пять секунд; повторить, когда долгая
транзакция кончится. `LOCAL` — до конца транзакции выкатки, дальше не живёт.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "444de76032e8"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b5e0c3f94a17"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Операции Этапа 1, у которых этап один, — с прежними именами, оставшимися у старых строк
#: (`dr_screen`, `serp_task`, `judge`).
MARK_DONORS = """
UPDATE usage_records SET stage = 'donors'
WHERE stage IS NULL
  AND operation IN ('by_country', 'serp', 'serp_search', 'keywords', 'site_judge',
                    'dr_screen', 'serp_task', 'judge')
"""

#: Сколько ждать замка таблицы расхода, прежде чем выкатка откажет.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

#: Операции продаж — всё, что начинается с `sales_` (`core/usage.SALES_OPERATIONS`).
MARK_SALES = """
UPDATE usage_records AS record
SET stage = CAST(known.stage AS stage)
FROM (VALUES ('sales\\_%', 'sales')) AS known (pattern, stage)
WHERE record.stage IS NULL AND record.operation LIKE known.pattern
"""


def upgrade() -> None:
    """Колонка этапа, провайдеры обхода и разметка однозначных строк."""
    op.execute(LOCK_TIMEOUT)
    op.execute("ALTER TYPE usageprovider ADD VALUE IF NOT EXISTS 'proxy'")
    op.execute("ALTER TYPE usageprovider ADD VALUE IF NOT EXISTS 'render'")
    op.add_column(
        "usage_records",
        sa.Column(
            "stage",
            postgresql.ENUM("donors", "advertisers", "sales", name="stage", create_type=False),
            nullable=True,
        ),
    )
    op.execute(MARK_DONORS)
    op.execute(MARK_SALES)


def downgrade() -> None:
    """Колонка снимается; значения `proxy` и `render` остаются в перечислении.

    Убрать значение из перечисления Postgres можно только пересозданием типа со всеми
    зависимостями — блокировка таблицы расхода на живой базе. Строки обхода с этими
    провайдерами перед откатом кода надо удалить: прежний код их не прочтёт.
    """
    op.execute(LOCK_TIMEOUT)
    op.drop_column("usage_records", "stage")
