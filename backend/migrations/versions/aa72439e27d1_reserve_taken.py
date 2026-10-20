"""резерв: кто взял домен из резерва на рассмотрение и когда

Revision ID: aa72439e27d1
Revises: c9c37509854f
Create Date: 2026-10-20 18:00:00.000000

Вопрос оператора 20.10.2026: прогон на 122 домена предложил 28, а 80 не прошли пороги —
«куда деваются остальные, можно ли выбирать из них при нехватке». Резерв — домены прогона,
которые не прошли (`run_verdicts`, с причиной), и «Взять на рассмотрение» кладёт такой домен
в очередь прогона руками. Строка кандидата помнит, кто её положил и когда: машина кладёт
только годных по вердикту прогона, а у взятого из резерва вердикт «не подходит», и решать
о нём человек должен, видя это.

Колонки новые и пустые: всех прежних кандидатов клала машина. Откат снимает их — прежний
код их не читает, а положенные из резерва кандидаты остаются в очереди обычными.

**Откат на код вердикта прогона (без резерва) — про строки `excluded`.** Код резерва пишет в
`run_verdicts` и исключённых гейтом: статус `excluded`, его нет среди `DonorStatus`. Код
вердикта прогона читает только вердикты порогов (`RunVerdicts.of` — статусы `DonorStatus`) и
на таких строках не падает; повторный подъём застаёт их на месте. Откатываясь на код, где
этого отбора нет, сначала удалить их — иначе `DonorStatus('excluded')` даёт ValueError в
`outreach review-queue`:

    DELETE FROM run_verdicts WHERE status = 'excluded';

**«Не проверен» из-за нехватки данных получает код причины** (`gap`, ревью 20.10.2026): код
вердикта прогона пишет «не проверен» без кода, и строки, записанные до резерва, в его группу «не
хватает данных» не попали бы. Подъём ставит им код по причине (`GAP_REASONS`); откат код снимает.
Значения заморожены на 20.10.2026 — ровно то, что код вердикта прогона писал без кода причины
(`rejudge.DATA_GAPS`, обрезанные по длине колонки); новые причины код резерва сразу пишет с
`cause = 'gap'`, и заполнять их не нужно. Ревизия — снимок: кода приложения она не импортирует.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ADD COLUMN и DROP COLUMN берут
ACCESS EXCLUSIVE на `run_candidates`, а её читают очередь, решения и отчёт прогона. За долгой
транзакцией прогона выкатка встала бы, а за ней — все эти запросы; с пределом она честно падает
за 5 с, и её повторяют (как в c9c37509854f и b6440cd714cc).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aa72439e27d1"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c9c37509854f"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

#: Строки вердиктов прогона — то, что ревизия читает и правит.
VERDICTS = sa.table("run_verdicts", sa.column("status"), sa.column("cause"), sa.column("reason"))
#: «Не проверен» — статус строки вердикта (`DonorStatus.UNCHECKED`); код причины нехватки
#: данных — `gap` (`runs.verdicts.GAP`). На 20.10.2026.
UNCHECKED = "unchecked"
GAP = "gap"
#: Причины нехватки данных, как их писал код вердикта прогона (`rejudge.DATA_GAPS`), уже
#: обрезанные по длине колонки `run_verdicts.reason` (128). На 20.10.2026.
GAP_REASONS: tuple[str, ...] = (
    "в разбивке одна верхняя страна — места страны прогона не видно",
    "куплен только DR: прежние пороги отсеяли домен по нему, других метрик нет",
    "страны не получены",
    "страны не спрашивали: прежние пороги отсеяли домен по метрикам",
)


def upgrade() -> None:
    """Кто и когда взял кандидата из резерва; у положенных прогоном — пусто."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("run_candidates", sa.Column("taken_by", sa.String(length=255), nullable=True))
    op.add_column(
        "run_candidates", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        VERDICTS.update()
        .where(VERDICTS.c.status == UNCHECKED)
        .where(VERDICTS.c.cause.is_(None))
        .where(VERDICTS.c.reason.in_(GAP_REASONS))
        .values(cause=GAP)
    )


def downgrade() -> None:
    """Снять пометку: взятые из резерва остаются в очереди обычными кандидатами; «не проверен»
    снова без кода причины, как его пишет код вердикта прогона."""
    op.execute(LOCK_TIMEOUT)
    op.execute(VERDICTS.update().where(VERDICTS.c.cause == GAP).values(cause=None))
    op.drop_column("run_candidates", "taken_at")
    op.drop_column("run_candidates", "taken_by")
