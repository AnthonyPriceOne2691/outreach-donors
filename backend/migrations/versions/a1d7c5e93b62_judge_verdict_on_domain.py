"""вердикт судьи площадки — на домене, а не на доноре

Revision ID: a1d7c5e93b62
Revises: c4e7a2b95f16
Create Date: 2026-09-23 12:00:00.000000

Поля ложатся на `domains`, и это не выбор удобства. В шапке модели домена
записано, ради чего она существует: «один и тот же сайт бывает донором
в Этапе 1 и рекламодателем в Этапе 2». Способ заработка — свойство сайта,
а не его роли:

- Этапу 1 `sells_own` означает «не донор»;
- Этапу 2 тот же `sells_own` означает лучшего кандидата в рекламодатели.

Положив вердикт на донора, мы спрятали бы его от второго этапа и заплатили
бы за него второй раз. Плюс отметка времени даёт кэш даром: повторный
прогон домен не пересуживает.

Решение человека живёт рядом отдельными полями и вердикт модели НЕ
переписывает: расхождение между ними — единственный измеритель того, как
часто она ошибается. Тот же приём уже работает в разборе ответов.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1d7c5e93b62'
down_revision: Union[str, Sequence[str], None] = 'c4e7a2b95f16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("domains", sa.Column("site_intent", sa.String(16), nullable=True))
    op.add_column("domains", sa.Column("judge_recommendation", sa.String(8), nullable=True))
    op.add_column("domains", sa.Column("judge_quote", sa.String(512), nullable=True))
    op.add_column("domains", sa.Column("judge_reason", sa.String(256), nullable=True))
    # Какую страницу судили. Без неё цитата повисает без контекста, а тип
    # страницы решает: замер 23.09 показал, что витрина и статья одного
    # и того же сайта дают разные вердикты.
    op.add_column("domains", sa.Column("judge_source_url", sa.String(1024), nullable=True))
    op.add_column("domains", sa.Column("judge_model", sa.String(64), nullable=True))
    op.add_column(
        "domains", sa.Column("judged_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Решение человека. Сильнее модели, но её вердикт не переписывает.
    op.add_column("domains", sa.Column("human_intent", sa.String(16), nullable=True))
    op.add_column(
        "domains", sa.Column("human_verdict_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("domains", sa.Column("human_note", sa.String(512), nullable=True))
    # Экран фильтрует по вердикту и по расхождению — без индекса это
    # последовательный проход по всей базе доменов.
    op.create_index(
        "idx_domains_judge_recommendation", "domains", ["judge_recommendation"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_domains_judge_recommendation", table_name="domains")
    for column in (
        "human_note",
        "human_verdict_at",
        "human_intent",
        "judged_at",
        "judge_model",
        "judge_source_url",
        "judge_reason",
        "judge_quote",
        "judge_recommendation",
        "site_intent",
    ):
        op.drop_column("domains", column)
