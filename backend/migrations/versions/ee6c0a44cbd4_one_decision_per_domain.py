"""рассмотрение: решение по домену одно — ждущие строки решённых доменов становятся копией

Revision ID: ee6c0a44cbd4
Revises: d2afdd4a00d0
Create Date: 2026-10-11 16:00:00.000000

Решение владельца 11.10.2026: «решение по домену должно быть одно». До него решение
ложилось только на строку своего прогона: домен, ждавший во втором прогоне, ждал там
и после решения в первом, считался в «Рассмотреть» второго, и решение там переворачивало
первое — побеждал последний щелчок. Теперь решение в одном прогоне ложится копией
на строки домена во всех остальных (`review.candidates`), а эта ревизия закрывает строки,
оставшиеся с прежних времён: ждущая строка домена, у донора которого решение есть
(`donors.review`), становится копией этого решения — `carried`, без автора, времени
и записки, такой же, какую кладёт очередь прогона.

Только данные, схема та же. Повтор ничего не меняет: закрытая строка уже не ждёт.

Откат пустой, и это намеренно. Ревизия не помнит, какие строки закрыла: у копии отсюда
и у копии из очереди один вид, и вернуть «предложен» значило бы открыть и копии,
положенные очередью, — то есть вернуть ту самую поломку. Прежний код такие строки
читает как свои: копии он клал и сам.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "ee6c0a44cbd4"
down_revision: Union[str, Sequence[str], None] = "d2afdd4a00d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Ждущая строка решённого домена — копия решения: статус донора, пометка «перенесено»,
#: ни автора, ни времени, ни записки (`review.candidates._copy_of`).
CLOSE_WAITING_ROWS = """
UPDATE run_candidates AS candidate
SET status = donor.review,
    carried = true,
    decided_by = NULL,
    decided_at = NULL,
    note = NULL,
    updated_at = now()
FROM donors AS donor
WHERE donor.domain_id = candidate.domain_id
  AND candidate.status = 'pending'
  AND donor.review IN ('accepted', 'rejected')
"""


def upgrade() -> None:
    """Ждущие строки решённых доменов — копией решения."""
    op.execute(CLOSE_WAITING_ROWS)


def downgrade() -> None:
    """Ничего: ревизия не помнит, какие строки закрыла (см. выше)."""
