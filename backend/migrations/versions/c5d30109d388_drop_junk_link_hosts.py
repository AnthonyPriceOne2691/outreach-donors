"""мусор вместо домена: строки ссылок, кандидаты и память обхода

Revision ID: c5d30109d388
Revises: 015f15107ded
Create Date: 2026-11-02 12:40:00.000000

Обход записывал хост ссылки как есть: шаблон на JavaScript, телефон, текст, имя без точки. С
02.11.2026 такой адрес до базы не доходит (`crawl/links.py`, грабли №6), а накопленное лежало:
ссылки, кандидаты, память донора. Кандидат с таким именем уходил в пакет DR, и Ahrefs отказывал
всему пакету; пересчёт из строк ссылок вернул бы мусор обратно, поэтому чистятся все три таблицы.

**Правило — своя копия, а не импорт из приложения:** миграция обязана значить одно и то же и
через год, когда код уйдёт вперёд. Совпадение с `donors/host.domain_name` на сегодня держит тест.

**По списку имён, а не регуляркой в базе:** буквы любого алфавита — категории Юникода, а в
Postgres их нет. Читается `DISTINCT` колонки, имена фильтруются здесь, удаление — пачками.

**Кандидаты — по корню, ссылки — по хосту.** У ссылки `мусор с пробелом.example.test` хост негоден,
а корень настоящий: ссылка уходит, кандидат домена остаётся. Повторный запуск ничего не меняет.

Отката нет: удалённое не восстанавливается и `downgrade` ничего не делает. Вернуть мусор значило
бы вернуть падения пакета DR, а настоящих доменов правило не трогает.
"""

import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d30109d388"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "015f15107ded"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Таблица и колонка с именем.
COLUMNS = (
    ("outlinks", "target_host"),
    ("advertiser_candidates", "target_root"),
    ("candidate_memory", "target_root"),
)

#: Имён за один запрос на удаление.
BATCH = 500


def _is_label(label: str) -> bool:
    """Метка имени: 1–63 знака, буквы и цифры любого алфавита, дефис не с края."""
    if not 1 <= len(label) <= 63 or label[0] == "-" or label[-1] == "-":
        return False
    return all(char in "-_" or unicodedata.category(char)[0] in "LMN" for char in label)


def _is_name(host: str) -> bool:
    """Доменное имя: есть точка, не длиннее 253 знаков, зона не из одних цифр. Точка в конце
    имени не мешает."""
    name = host.removesuffix(".")
    labels = name.split(".")
    if len(labels) < 2 or len(name) > 253 or labels[-1].isdigit():
        return False
    return all(_is_label(label) for label in labels)


def _clean(connection: sa.Connection, table: str, column: str) -> None:
    """Удалить строки таблицы, у которых в колонке не доменное имя."""
    name = sa.column(column, sa.String)
    rows = sa.table(table, name)
    found = connection.scalars(sa.select(name).select_from(rows).distinct())
    junk = sorted(host for host in found if not _is_name(host))
    for start in range(0, len(junk), BATCH):
        connection.execute(rows.delete().where(name.in_(junk[start : start + BATCH])))


def upgrade() -> None:
    """Мусор — из ссылок, кандидатов и памяти донора."""
    connection = op.get_bind()
    for table, column in COLUMNS:
        _clean(connection, table, column)


def downgrade() -> None:
    """Ничего: удалённый мусор не восстанавливается (шапка файла)."""
