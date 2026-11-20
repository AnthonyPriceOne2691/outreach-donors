"""журнал действий только дописывается: UPDATE, DELETE и TRUNCATE — отказ самой базы

Revision ID: d4f8a2c61e93
Revises: c0cf586c2995
Create Date: 2026-11-20 12:00:00.000000

Аудит 20.11.2026, п. 36. Запись журнала не правится и не удаляется — так задумано с первого дня
(`core/models/access.py`), но держал это только код: строку журнала переписал бы или стёр любой,
у кого есть сессия в базе, — ошибка в коде, внедрённый SQL, рука в psql. Теперь держит база:
UPDATE и DELETE строки `audit_log` и TRUNCATE таблицы отвечают отказом `42501` со словами.

**UPDATE и DELETE — на каждую строку, TRUNCATE — на команду** (у TRUNCATE строк нет). Команда,
которая не задела ни одной строки журнала, проходит: отказ — когда меняется запись. Поэтому
удаление учётки, на которую ссылается журнал, — тоже отказ (`ON DELETE SET NULL` стёр бы автора
записи), а учётки без записей — нет. Учётки и так не удаляются, а отключаются.

**Обход — только у суперпользователя, и только осознанно.** Триггер обычный (`ENABLE`, а не
`ENABLE ALWAYS`): сессия с `SET LOCAL session_replication_role = replica` его не зовёт. Задать
эту настройку может только суперпользователь — владелец схемы; роль приложения
(`shared/database/app_role.py`) не может, а UPDATE, DELETE и TRUNCATE журнала у неё отобраны и
правами. Так чистят базу между тестами оснастки с настоящими фиксациями
(`tests/committed_db.py`). Восстановлению копии триггер не мешает: `pg_restore` строки вставляет,
таблицы сносит `DROP`, а триггер заводит после данных (`tests/test_dump_round_trip.py`).

Пока сервисы ходят владельцем (пароль роли приложения не задан), замок защищает от ошибки кода
и руки, но не от внедрённого SQL: суперпользователь снимет и триггер. Защиту от внедрённого SQL
даёт роль приложения.

`downgrade` снимает триггеры и функцию: журнал снова правится, как до ревизии.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4f8a2c61e93"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "c0cf586c2995"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FUNCTION = "audit_log_append_only"
ROW_TRIGGER = "audit_log_append_only_rows"
TRUNCATE_TRIGGER = "audit_log_append_only_truncate"


def upgrade() -> None:
    """Upgrade schema."""
    # Код 42501 (insufficient_privilege), а не 23…: обработчик API читает класс 23 как гонку
    # двух запросов и ответил бы «обновите экран». Правка журнала — поломка, а не гонка.
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN
            RAISE EXCEPTION 'журнал действий только дописывается: % в audit_log запрещён', TG_OP
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'аудит 20.11.2026, ревизия d4f8a2c61e93';
        END
        $body$
        """
    )
    op.execute(
        f"CREATE TRIGGER {ROW_TRIGGER} BEFORE UPDATE OR DELETE ON audit_log "
        f"FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON audit_log "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON audit_log")
    op.execute(f"DROP TRIGGER IF EXISTS {ROW_TRIGGER} ON audit_log")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
