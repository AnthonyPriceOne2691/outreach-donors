"""ключ домена в ASCII: имена сайтов письменностью — в форме xn--

Revision ID: b7e4c2a9d1f3
Revises: ed07677f52ff
Create Date: 2026-11-19 13:00:00.000000

Один сайт приходил в базу двумя ключами: «пример.рф» из ссылки или файла и `xn--e1afmkfd.xn--p1ai` из
выдачи — две записи донора, два кандидата, две строки памяти. С 19.11.2026 ключ домена — всегда ASCII
(`donors/host.normalize_host`, `crawl/links`, `contacts/sweep_input.host_from_cell`), а записанное
письменностью раньше переводится здесь: доноры, кандидаты, память и суждения, рекламодатели, обход,
ссылки и стоп-лист продаж.

**Своя копия перевода, а не импорт из приложения** (как у `c5d30109d388`): миграция обязана значить
одно и то же и через год. Совпадение с `shared/net/idn.ascii_host` на сегодня держит тест.

**По списку имён:** база отдаёт только имена, где байтов больше, чем знаков, — то есть не ASCII;
перевод — здесь, правка — одной командой на колонку с таблицей соответствия. У `outlinks.target_host`
индекса нет: запрос на каждое имя проходил бы таблицу заново.

**Двойник не сливается.** Если та же запись в ASCII уже есть (уникальный ключ), имя в этой колонке
остаётся как было, и журнал миграции его называет: слить донора или память — решение человека, а не
миграции. Имя, которое не переводится (знак вне IDNA, метка длиннее 63 знаков, имя длиннее 253), —
тоже как было и тоже в журнал. Повторный запуск ничего не меняет.

Отката нет: ASCII — та же запись, что и письменность, и `downgrade` ничего не делает.
"""

import logging
from typing import Sequence, Union

import idna
import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c2a9d1f3"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "ed07677f52ff"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

#: Таблица и колонка с именем хоста. Доноры — первыми: на их имя смотрят остальные строки.
COLUMNS = (
    ("domains", "host"),
    ("advertiser_candidates", "target_root"),
    ("advertiser_candidates", "donor_host"),
    ("candidate_memory", "target_root"),
    ("candidate_memory", "donor_host"),
    ("advertiser_judgements", "target_root"),
    ("advertiser_judgements", "donor_host"),
    ("advertisers", "best_donor_host"),
    ("supplier_donors", "host"),
    ("crawl_runs", "host"),
    ("crawl_depths", "host"),
    ("crawl_backlog", "host"),
    ("donor_load_hosts", "host"),
    # Стоп-лист продаж сравнивается строкой (ревью продаж 19.11): запись письменностью перестала
    # бы держать лида, чей домен теперь в ASCII.
    ("sales_stoplist", "host"),
    ("outlinks", "target_root"),
    ("outlinks", "target_host"),
)

#: Потолок доменного имени (RFC 1035) — и колонки `domains.host`.
MAX_NAME = 253


def _ascii(host: str) -> str | None:
    """Имя в ASCII: метка не в ASCII — IDNA 2008 по UTS 46 без переходной схемы, прочие — как есть.
    Не переводится — `None`."""
    labels = host.split(".")
    if "" in labels:
        return None
    try:
        name = ".".join(
            label if label.isascii() else idna.encode(label, uts46=True).decode("ascii")
            for label in labels
        )
    except (idna.IDNAError, UnicodeError):
        return None
    return name if len(name) <= MAX_NAME else None


def _pairs(connection: sa.Connection, table: str, column: str) -> dict[str, str]:
    """Имена колонки не в ASCII → их ASCII; непереводимые — в журнал, без пары."""
    name = sa.column(column, sa.String)
    rows = sa.table(table, name)
    found = connection.scalars(
        sa.select(name)
        .select_from(rows)
        .where(sa.func.octet_length(name) != sa.func.char_length(name))
        .distinct()
    )
    pairs: dict[str, str] = {}
    for host in found:
        code = _ascii(host)
        if code is None:
            logger.warning(
                "ключ домена в ASCII: %s.%s «%s» не переводится — как было", table, column, host
            )
        else:
            pairs[host] = code
    return pairs


def _rename(connection: sa.Connection, table: str, column: str, pairs: dict[str, str]) -> None:
    """Имена колонки — в ASCII одной командой; уникальный ключ не пустил — по одному имени."""
    if not pairs:
        return
    target = sa.table(table, sa.column(column, sa.String))
    mapping = sa.values(
        sa.column("name", sa.String), sa.column("code", sa.String), name="names"
    ).data(sorted(pairs.items()))
    try:
        with connection.begin_nested():
            connection.execute(
                target.update()
                .where(target.c[column] == mapping.c.name)
                .values({column: mapping.c.code})
            )
        return
    except sa.exc.IntegrityError:
        pass
    for host, code in sorted(pairs.items()):
        try:
            with connection.begin_nested():
                connection.execute(
                    target.update().where(target.c[column] == host).values({column: code})
                )
        except sa.exc.IntegrityError:
            logger.warning(
                "ключ домена в ASCII: %s.%s «%s» остался как был — запись «%s» уже есть",
                table,
                column,
                host,
                code,
            )


def upgrade() -> None:
    """Имена письменностью — в ASCII во всех колонках с хостом."""
    connection = op.get_bind()
    for table, column in COLUMNS:
        _rename(connection, table, column, _pairs(connection, table, column))


def downgrade() -> None:
    """Ничего: ASCII — та же запись (шапка файла)."""
