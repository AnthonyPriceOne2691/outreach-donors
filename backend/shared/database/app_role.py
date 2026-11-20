"""Роль приложения в базе: заводится и держит свои права (аудит 20.11.2026, п. 31).

    python -m backend.shared.database.app_role          # завести роль и выдать права
    python -m backend.shared.database.app_role --check  # кем ходит сервис и что ему можно

Первую команду запускает контейнер `migrate` сразу после `alembic upgrade head`, владельцем
схемы (`storage.OWNER_DSN`), — при каждой выкатке и после каждого восстановления копии:
`scripts/restore.sh` зовёт тот же `migrate`. Вторую — человек после переключения
(`deploy/README.md`): её адрес тот же, что у сервисов (`storage.DSN`).

**Почему шагом `migrate`, а не ревизией alembic.** Роль — объект кластера, а не базы: дамп
базы её не несёт, ревизия исполняется один раз за жизнь базы, а права нужны и после
восстановления копии на новой машине, и на каждой таблице, которую заведут потом. Пароль —
из окружения, а ревизия, читающая секрет, значила бы разное на разных машинах. Шаг повторяем:
каждый запуск приводит роль и права к одному виду, и смена пароля — правка `.env` и выкатка.

**Что у роли.** Вход; без суперправ, без создания баз и ролей, без наследования чужих прав,
без репликации и обхода RLS. Строки всех таблиц схемы `public` — чтение и запись,
последовательности — следующий номер; таблицы и последовательности, которые миграции заведут
потом, получают те же права сами (`ALTER DEFAULT PRIVILEGES` владельца). Журнал действий —
только чтение и добавление (его держит и триггер ревизии `d4f8a2c61e93`), служебную таблицу
alembic пишут только миграции. CREATE, ALTER, DROP и TRUNCATE, COPY с файлами и программами
машины базы, функции администратора — отказ: роль не владеет ни одной таблицей.

**Пароль не печатается и не уходит в базу открытым текстом.** В `ALTER ROLE` идёт
проверочная строка SCRAM-SHA-256, посчитанная здесь (так делает и `\\password` у psql):
журнал базы, `pg_stat_activity` и текст отказа видят только её.

Пароль пуст — роль не трогается, а сервисы ходят владельцем (`config/storage.py`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
import sys
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import storage
from backend.shared.logs import setup_logging

logger = logging.getLogger(__name__)

#: Атрибуты роли — заново при каждом запуске: суперправа или создание баз, выданные роли
#: руками, не переживают следующую выкатку.
ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
TABLE_RIGHTS = "SELECT, INSERT, UPDATE, DELETE"
SEQUENCE_RIGHTS = "USAGE, SELECT, UPDATE"

#: Таблица → что у роли отобрано поверх общих прав. Журнал действий только дописывается,
#: служебную таблицу alembic пишут только миграции.
NARROWED = {
    "audit_log": "UPDATE, DELETE",
    "alembic_version": "INSERT, UPDATE, DELETE",
}

#: Замок выдачи прав: два `migrate` разом (выкатка и ручной запуск) иначе правили бы одни и те
#: же строки каталога, и один из них падал бы «tuple concurrently updated». «APPROLE» в ASCII.
GRANT_LOCK = 0x415050524F4C45

#: Итераций SCRAM — как у самого Postgres (`scram_iterations`, умолчание 4096).
SCRAM_ITERATIONS = 4096

_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")

#: Таблица, на которой проверка смотрит запись строк и номер последовательности (`_CHECK`).
_PROBE_TABLE = "public.domains"


class AppRoleError(ValueError):
    """Пароль или имя роли, с которыми шаг не работает, — словами, без самого пароля."""


def scram_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """Проверочная строка SCRAM-SHA-256 (RFC 5802, RFC 7677) — то, что Postgres хранит вместо
    пароля и принимает в `PASSWORD '…'` как есть.

    Только печатный ASCII: перед хешем Postgres нормализует пароль (SASLprep), и строка,
    посчитанная здесь из другого текста, разошлась бы с той, что посчитает драйвер при входе."""
    if not (password.isascii() and password.isprintable()):
        raise AppRoleError(
            "POSTGRES_APP_PASSWORD: только печатные знаки ASCII — их одинаково хеширует и выдача "
            "прав, и драйвер при входе; сгенерировать: openssl rand -hex 24"
        )
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    salt64, stored64, server64 = (
        base64.b64encode(raw).decode("ascii") for raw in (salt, stored_key, server_key)
    )
    return f"SCRAM-SHA-256${iterations}:{salt64}${stored64}:{server64}"


def _statements(who: str, database: str, narrowed: dict[str, str]) -> list[str]:
    """Права роли `who` (имя уже в кавычках) — сначала всё прочь, потом ровно задуманное:
    TRUNCATE, TRIGGER или CREATE, выданные руками, не переживают выкатку."""
    return [
        f"GRANT CONNECT ON DATABASE {database} TO {who}",
        # Postgres до 15-го давал всем CREATE в `public`: база, перенесённая со старого
        # кластера, несла бы это право и роли приложения.
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"REVOKE ALL ON SCHEMA public FROM {who}",
        # Явно: схема, пересозданная командой `CREATE SCHEMA public` (так чистит базу оснастка
        # тестов), не даёт USAGE никому, кроме владельца.
        f"GRANT USAGE ON SCHEMA public TO {who}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {who}",
        f"GRANT {TABLE_RIGHTS} ON ALL TABLES IN SCHEMA public TO {who}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {who}",
        f"GRANT {SEQUENCE_RIGHTS} ON ALL SEQUENCES IN SCHEMA public TO {who}",
        # Таблицы будущих миграций: их заводит тот же владелец, что сейчас выдаёт права.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {who}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {TABLE_RIGHTS} ON TABLES TO {who}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {who}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {SEQUENCE_RIGHTS} ON SEQUENCES TO {who}",
        *(f"REVOKE {rights} ON public.{table} FROM {who}" for table, rights in narrowed.items()),
    ]


async def ensure(conn: AsyncConnection, *, role: str, password: str) -> None:
    """Завести роль `role` или вернуть её к задуманному виду, задать пароль и выдать права — в
    транзакции `conn`: другие сессии видят итог целиком, после фиксации, а не по шагам."""
    if not _ROLE_NAME.fullmatch(role):
        raise AppRoleError(
            f"имя роли {role!r}: строчные латинские буквы, цифры и «_», до 63 знаков"
        )
    verifier = scram_verifier(password)
    quote = conn.dialect.identifier_preparer.quote_identifier
    who = quote(role)
    await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GRANT_LOCK})
    found = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
    # Служебные команды Postgres параметров не принимают — текст собирается здесь: имя проверено
    # выше и в кавычках, в проверочной строке только base64 и знаки «$:». Мимо разбора `text()`:
    # двоеточие в ней он принял бы за параметр.
    verb = "ALTER" if found else "CREATE"
    await conn.exec_driver_sql(f"{verb} ROLE {who} WITH {ATTRIBUTES} PASSWORD '{verifier}'")
    database = quote(str(await conn.scalar(text("SELECT current_database()"))))
    present = set(
        await conn.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"),
        )
    )
    narrowed = {table: rights for table, rights in NARROWED.items() if table in present}
    for statement in _statements(who, database, narrowed):
        await conn.exec_driver_sql(statement)


_CHECK = text(
    """
    SELECT current_user AS who,
           r.rolsuper AS superuser,
           r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls AS admin,
           has_database_privilege(current_database(), 'CREATE')
             OR has_schema_privilege('public', 'CREATE') AS creates,
           has_table_privilege('public.domains', 'SELECT')
             AND has_table_privilege('public.domains', 'INSERT')
             AND has_table_privilege('public.domains', 'UPDATE')
             AND has_table_privilege('public.domains', 'DELETE') AS writes,
           has_sequence_privilege(
             pg_get_serial_sequence('public.domains', 'id'), 'USAGE') AS numbers,
           has_table_privilege('public.audit_log', 'UPDATE, DELETE, TRUNCATE') AS rewrites,
           EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid = 'public.audit_log'::regclass AND NOT tgisinternal) AS locked
      FROM pg_roles r
     WHERE r.rolname = current_user
    """
)


async def problems(conn: AsyncConnection, role: str) -> list[str]:
    """Чем сессия `conn` расходится с задуманной ролью приложения `role` — словами; пусто — ничем.

    Только каталог и функции прав: проверка идёт и на живой базе, где пробный CREATE оставил бы
    строку отказа в журнале базы."""
    row = (await conn.execute(_CHECK)).one()
    said = [
        (row.who != role, f"сессия — роль «{row.who}», а не «{role}»: пароль роли не задан?"),
        (row.superuser, "у роли суперправа"),
        (row.admin, "роль может создавать базы или роли, реплицировать или обходить RLS"),
        (row.creates, "роль может создавать схемы или таблицы"),
        (not row.writes, f"роль не пишет строки ({_PROBE_TABLE})"),
        (not row.numbers, f"роль не берёт номер последовательности ({_PROBE_TABLE})"),
        (row.rewrites, "роль может править или стирать журнал действий"),
        (not row.locked, "у журнала действий нет триггера: ревизия d4f8a2c61e93 не применена?"),
    ]
    return [words for wrong, words in said if wrong]


async def _ensure() -> int:
    role, password = storage.APP_ROLE, storage.APP_PASSWORD
    if not password:
        logger.info(
            "пароль роли приложения не задан (POSTGRES_APP_PASSWORD) — роль не трогается, "
            "сервисы ходят владельцем схемы",
            extra={"role": role},
        )
        return 0
    engine = create_async_engine(storage.OWNER_DSN, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await ensure(conn, role=role, password=password)
    except AppRoleError as exc:
        logger.error(  # noqa: TRY400 — отказ сказан словами, трассировка к ним ничего не добавит
            "роль приложения не заведена", extra={"role": role, "error": str(exc)}
        )
        return 1
    except DBAPIError as exc:
        # Текст отказа SQLAlchemy и его трассировка несут саму команду — с проверочной строкой
        # пароля. В журнал — только первая строка ответа базы.
        refused = next(iter(str(exc.orig).splitlines()), "") or type(exc.orig).__name__
        logger.error(  # noqa: TRY400 — трассировка напечатала бы команду с проверочной строкой
            "роль приложения: база отказала", extra={"role": role, "error": refused}
        )
        return 1
    finally:
        await engine.dispose()
    logger.info("роль приложения заведена, права выданы", extra={"role": role})
    return 0


async def _check() -> int:
    engine = create_async_engine(storage.DSN, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            found = await problems(conn, storage.APP_ROLE)
    finally:
        await engine.dispose()
    if found:
        print("Сервис ходит в базу не так, как задумано:\n  " + "\n  ".join(found))
        return 1
    print(
        f"Сервис ходит ролью «{storage.APP_ROLE}»: без суперправ и DDL, строки пишет, "
        "журнал действий только дописывает."
    )
    return 0


def main(argv: Sequence[str]) -> int:
    setup_logging()
    if not argv:
        return asyncio.run(_ensure())
    if list(argv) == ["--check"]:
        return asyncio.run(_check())
    print("Без доводов — завести роль приложения и выдать права; --check — кем ходит сервис.")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
