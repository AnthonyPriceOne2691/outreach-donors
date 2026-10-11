"""письма доноров: почтовый адрес строкой подписи — и в утверждённых текстах рассылок

Revision ID: 5b1e7c9a3d20
Revises: ee6c0a44cbd4
Create Date: 2026-10-11 23:00:00.000000

Решение Anthony 11.10.2026: почтовый адрес отправителя стоит в каждом письме доноров —
строкой подписи под именем. Шаблоны в коде получили `{{postal_address}}`, и подпись без
него разбор теперь отвергает (`template.REQUIRED_IN_ZONE`). Рассылка же хранит текст
первого письма целиком (`campaigns.letter_template`), и тексты, утверждённые раньше,
адреса не знают: без этой ревизии письма идущих рассылок уходили бы без адреса, а их
сборка встала бы отказом разбора.

Ревизия дописывает строку `{{postal_address}}` в конец зоны подписи, и только её:
остальной утверждённый текст не трогается. Текст, где адрес уже есть, или без зоны
подписи остаётся как был, поэтому повтор ничего не меняет.

Откат пустой, и это намеренно. Прежний код подпись с адресом читает: подстановка
`postal_address` у него есть и заполняется из `OUTREACH_POSTAL_ADDRESS`. А вычеркнуть
строку значило бы вычеркнуть и адрес, утверждённый в рассылках после этой ревизии:
ревизия не помнит, где строку дописала она.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5b1e7c9a3d20"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "ee6c0a44cbd4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Строка адреса в подписи — как в шаблонах кода.
ADDRESS = "{{postal_address}}"

#: Заголовок зоны — тем же правилом, что разбор шаблона (`template.HEADER_RE`), но своей
#: копией: ревизия не должна меняться вместе с кодом.
HEADER = re.compile(r"^\[([a-z_]+)\]\s+(\w+)\s*$")


def with_address(text: str) -> str:
    """Текст рассылки со строкой адреса в конце зоны подписи.

    Адрес уже есть или зоны подписи нет — текст как был. Пустые строки в конце зоны
    остаются после адреса: вид текста, кроме одной строки, не меняется."""
    if ADDRESS in text:
        return text
    lines = text.split("\n")
    start = next(
        (n for n, line in enumerate(lines) if (m := HEADER.match(line)) and m[1] == "signature"),
        None,
    )
    if start is None:
        return text
    end = next((n for n in range(start + 1, len(lines)) if HEADER.match(lines[n])), len(lines))
    last = end - 1
    while last > start and not lines[last].strip():
        last -= 1
    lines.insert(last + 1, ADDRESS)
    return "\n".join(lines)


def upgrade() -> None:
    """Строка адреса — в подпись утверждённых текстов рассылок, где её нет."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, letter_template FROM campaigns WHERE letter_template IS NOT NULL")
    ).all()
    for campaign_id, stored in rows:
        changed = with_address(stored)
        if changed != stored:
            bind.execute(
                sa.text(
                    "UPDATE campaigns SET letter_template = :text, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"text": changed, "id": campaign_id},
            )


def downgrade() -> None:
    """Ничего: прежний код подпись с адресом читает (см. выше)."""
