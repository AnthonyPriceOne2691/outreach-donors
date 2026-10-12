"""продажи: сбор новой базы — прогоны гипотезы, кандидаты сбора и вердикты судьи сегмента

Revision ID: ed31d48a1ec2
Revises: 444de76032e8
Create Date: 2026-10-12 00:30:00.000000

Срез 6.1: ключи → выдача → судья сегмента → адрес лестницей → лиды гипотезы. Три таблицы
продаж и новый источник лида:

- `sales_segment_runs` — прогон гипотезы: ключи, рынок, язык писем, глубина, режим судьи,
  смета на запуске и числа этапов;
- `sales_segment_candidates` — домен × гипотеза: текст выдачи, полоса судьи и человека,
  исход поиска адреса (колонки общей схемы попыток, тип `contactstatus` — уже есть),
  лид из кандидата;
- `sales_segment_verdicts` — вердикт судьи сегмента: домен, гипотеза, версия промпта,
  отпечаток описания сегмента, вердикт, цитата, причина, модель, дата;
- значение `collected` источника лида (`sales_lead_source`): в этой ревизии не пишется.

Ссылки на общие таблицы: домен — каскадом (кандидата и вердикт снова даст выдача и судья,
лида домен держит сам), лид — гаснет. Подъём только создаёт — данных не трогает; откат
переводит лидов сбора в `import`: старый код значения `collected` не знает, и список лидов
на их строке отдал бы 500.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`): ключ на `domains` при создании
таблицы берёт замок SHARE ROW EXCLUSIVE, и за долгой транзакцией обходчика выкатка встала бы,
а за ней — каждый, кто пишет в `domains`. С пределом выкатка падает словами через пять секунд;
повторить, когда долгая транзакция кончится. `LOCAL` — до конца транзакции выкатки.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ed31d48a1ec2"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "444de76032e8"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

_CONTACT_STATUS = (
    "found", "not_found", "form_only", "no_quota", "rate_limited", "error", "blocked", "no_answer",
)  # fmt: skip


def _when() -> list[sa.Column]:
    """`created_at` и `updated_at` примеси `TimestampedMixin` — своими колонками у таблицы."""
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def _attempts() -> list[sa.Column]:
    """Колонки общей схемы попыток поиска адреса (`ContactAttemptMixin`). Тип исхода уже
    есть — его завела начальная схема доноров; `create_type=False` поэтому."""
    status = postgresql.ENUM(*_CONTACT_STATUS, name="contactstatus", create_type=False)
    return [
        sa.Column("contact_status", status, nullable=True),
        sa.Column("contact_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_tries", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("contact_reason", sa.Text(), nullable=True),
    ]


def upgrade() -> None:
    """Таблицы сбора и источник лида `collected`."""
    op.execute(LOCK_TIMEOUT)
    op.execute("ALTER TYPE sales_lead_source ADD VALUE IF NOT EXISTS 'collected'")
    op.create_table(
        "sales_segment_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("depth_pages", sa.SmallInteger(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_when(),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sales_segment_runs_hypothesis", "sales_segment_runs", ["hypothesis_id"], unique=False
    )
    op.create_table(
        "sales_segment_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("page_title", sa.Text(), nullable=True),
        sa.Column("page_description", sa.Text(), nullable=True),
        sa.Column("site_text", sa.Text(), nullable=True),
        sa.Column("lane", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("unanswered", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user", sa.String(length=255), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        *_attempts(),
        *_when(),
        sa.ForeignKeyConstraint(["run_id"], ["sales_segment_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["sales_leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "domain_id", "hypothesis_id", name="uq_sales_segment_candidates_domain"
        ),
    )
    op.create_index(
        "idx_sales_segment_candidates_run_lane",
        "sales_segment_candidates",
        ["run_id", "lane"],
        unique=False,
    )
    op.create_index(
        "idx_sales_segment_candidates_lead", "sales_segment_candidates", ["lead_id"], unique=False
    )
    op.create_index(
        "idx_sales_segment_candidates_hypothesis",
        "sales_segment_candidates",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_table(
        "sales_segment_verdicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain_id", sa.Integer(), nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("segment_digest", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=False),
        sa.Column("tokens", sa.Integer(), server_default="0", nullable=False),
        *_when(),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["sales_hypotheses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "domain_id",
            "hypothesis_id",
            "prompt_version",
            "segment_digest",
            name="uq_sales_segment_verdicts_key",
        ),
    )
    op.create_index(
        "idx_sales_segment_verdicts_hypothesis",
        "sales_segment_verdicts",
        ["hypothesis_id"],
        unique=False,
    )


def downgrade() -> None:
    """Снять таблицы сбора — в обратном порядке ссылок.

    Значение `collected` из перечисления не убирается — как `hunter` у провайдера расхода
    (2d9877260a6d): только пересозданием типа со всеми зависимостями. Поэтому лиды сбора
    переходят в `import`: старый код значения `collected` не прочтёт.
    """
    op.execute(LOCK_TIMEOUT)
    op.execute("UPDATE sales_leads SET source = 'import' WHERE source = 'collected'")
    op.drop_index("idx_sales_segment_verdicts_hypothesis", table_name="sales_segment_verdicts")
    op.drop_table("sales_segment_verdicts")
    op.drop_index("idx_sales_segment_candidates_hypothesis", table_name="sales_segment_candidates")
    op.drop_index("idx_sales_segment_candidates_lead", table_name="sales_segment_candidates")
    op.drop_index("idx_sales_segment_candidates_run_lane", table_name="sales_segment_candidates")
    op.drop_table("sales_segment_candidates")
    op.drop_index("idx_sales_segment_runs_hypothesis", table_name="sales_segment_runs")
    op.drop_table("sales_segment_runs")
