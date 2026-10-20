"""адрес контактной формы, поиск адреса глубже, письмо для формы

Revision ID: a7c3e9f15b20
Revises: aa72439e27d1
Create Date: 2026-10-20 18:00:00.000000

Три вещи под «Формы» и «Адреса нет» (20.10.2026):

- `domains.contact_form_url` — страница с формой, которую нашла лестница адресов:
  на «Формах» человек открывает её прямо, а не ищет по сайту. Форма — свойство сайта,
  как и адрес, поэтому колонка у домена. Старые строки — пустые: адрес формы до этой
  ревизии не запоминался, его даст следующий поиск;
- `donors.contact_deeper_at`, `donors.contact_deeper_note` — поиск глубже по кнопке:
  когда был и чем кончился словами. Пустые — глубже не искали;
- `form_letters` — текст письма для формы сайта: вызов модели стоит денег, текст
  хранится, и второй раз его открывают бесплатно.

Вниз — колонки и таблица уходят целиком: это память о найденном и собранном, а не
решение человека; прежний код их не читает.

**Замок — с пределом ожидания** (`SET LOCAL lock_timeout`), как у ed31d48a1ec2 и
444de76032e8: добавить и снять колонку — исключительный замок `domains` и `donors`, а в
них пишут обход, прогоны и поиск адресов. Долгая чужая транзакция без предела держала бы
выкатку, а за выкаткой — каждого, кто пишет в эти таблицы. С пределом выкатка падает
словами через пять секунд; повторить, когда долгая транзакция кончится. `LOCAL` — до
конца транзакции выкатки.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f15b20"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "aa72439e27d1"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Сколько ждать замка `domains` и `donors`, прежде чем выкатка откажет.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def _stamps() -> list[sa.Column]:
    """Когда строку завели и когда меняли — как у примеси моделей (`TimestampedMixin`).

    Свои колонки, а не импорт из кода приложения: ревизия читается и катится сама по себе,
    какой бы ни была модель к моменту выкатки."""
    return [
        sa.Column(stamp, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        for stamp in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    """Колонки формы и поиска глубже, таблица писем для форм."""
    op.execute(LOCK_TIMEOUT)
    op.add_column("domains", sa.Column("contact_form_url", sa.String(1024), nullable=True))
    op.add_column(
        "donors", sa.Column("contact_deeper_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("donors", sa.Column("contact_deeper_note", sa.String(512), nullable=True))
    op.create_table(
        "form_letters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "domain_id",
            sa.Integer(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("uniqueness_pct", sa.Float(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("based_on", sa.String(300), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("built_by", sa.String(255), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "stage", name="uq_form_letters_domain_stage"),
    )


def downgrade() -> None:
    """Снять таблицу и колонки."""
    op.execute(LOCK_TIMEOUT)
    op.drop_table("form_letters")
    op.drop_column("donors", "contact_deeper_note")
    op.drop_column("donors", "contact_deeper_at")
    op.drop_column("domains", "contact_form_url")
