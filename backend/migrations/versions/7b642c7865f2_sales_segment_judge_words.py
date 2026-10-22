"""слова, время и решавший судьи у сайта сбора — свои колонки

Revision ID: 7b642c7865f2
Revises: abcc1e4edf4a
Create Date: 2026-10-22 13:00:00.000000

Решение человека по сайту сбора затирает причину («отказал человек»), время и «кто решил» своими, а
слова судьи у решённого человеком сайта («Судья: …») и при отмене решения брались только из кэша
вердиктов. Вердикт, чья цитата не нашлась в тексте сайта, в кэш не ложится — и у такого сайта после
решения выходило «Судья: причина не сохранилась», а после отмены — «модель» без даты. Кто решал до
человека, отмена угадывала: по модели вердикта, а язык сайта — повтором сверки по сохранённому
тексту; правило языка поменялось — сайт, которому отказал язык, вернулся бы «отсечённым» правилом.
Теперь слова, время и решавший судьи — свои колонки сайта (`sales_segment_candidates.judge_reason`,
`judged_at`, `judged_by`): их пишет только судья — проход и пересуд, — а решение человека не трогает
(`sales/segment_window.py`, `sales/segment_report.py`).

Колонки новые и пустые: строки не переписываются (ADD COLUMN без умолчания). У сайта, судимого
раньше, слова — в причине, время — в «когда решено», решавший — в «кто решил»: решение человека
сначала переносит их в колонки судьи, потом пишет своё. Слова, уже затёртые решением до этой
ревизии, не восстанавливаются — у таких сайтов «Судья: …» берёт кэш, а нет и там — «причина не
сохранилась», как было; решавшего отмена угадывает, как раньше. В миграции — только литералы: код
приложения меняется, а ревизия должна подниматься и откатываться такой, какой её записали.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN и DROP COLUMN берут ACCESS
EXCLUSIVE на `sales_segment_candidates`, а строки сайтов пишут проход судьи и заведение лидов
(держит строки принятых до конца очистки адресов), отчёт прогона читает их каждые три секунды. За
долгой транзакцией выкатка встала бы, а за ней — весь сбор; с пределом она честно падает за 5 с, и
её повторяют.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b642c7865f2"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "abcc1e4edf4a"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
TABLE = "sales_segment_candidates"
#: Ширина кода решавшего — как у `decided_by` (`segment_models.CODE_LENGTH`), литералом.
CODE_LENGTH = 16


def upgrade() -> None:
    """Слова, время и решавший судьи у сайта сбора — пустые колонки."""
    op.execute(LOCK_TIMEOUT)
    op.add_column(TABLE, sa.Column("judge_reason", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("judged_by", sa.String(CODE_LENGTH), nullable=True))


def downgrade() -> None:
    """Снять колонки: прежний код их не знает — слова судьи у решённых человеком сайтов снова только
    в кэше вердиктов, а нет там — «причина не сохранилась»; решавшего отмена снова угадывает.
    Причина, время и «кто решил» сайта целы."""
    op.execute(LOCK_TIMEOUT)
    op.drop_column(TABLE, "judged_by")
    op.drop_column(TABLE, "judged_at")
    op.drop_column(TABLE, "judge_reason")
