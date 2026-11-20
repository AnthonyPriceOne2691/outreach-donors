"""Хранилище: база и очередь.

**Две роли базы** (аудит 20.11.2026, п. 31). `STORAGE_DSN` — владелец схемы, суперпользователь
образа Postgres: под ним идут миграции (`migrations/env.py`) и выдача прав роли приложения
(`shared/database/app_role.py`) — оба шага контейнера `migrate`. Сервисы — сервер, воркеры,
обходчики, разбор и добивки — ходят ролью приложения `outreach_app`, когда задан её пароль
`POSTGRES_APP_PASSWORD`: хост, порт и база те же, что у владельца, имя и пароль свои. У роли
нет суперправ и нет DDL — внедрённый в запрос SQL не создаёт и не сносит таблиц, не правит
журнал действий, не читает файлов машины базы и не запускает на ней программ.

Пароль пуст — `DSN` равен адресу владельца, и сервисы ходят им, как до 20.11.2026: выкатка
этого кода не требует правки `.env`, а переключение — шаги владельца в `deploy/README.md`.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field

from backend.config._base import DomainSettings

#: Имя роли приложения. В коде, а не в окружении: его знают выдача прав, проверка и документы.
APP_ROLE = "outreach_app"


class _Storage(DomainSettings):
    dsn: str = Field(
        default="postgresql+asyncpg://outreach:outreach@localhost:5442/outreach",
        validation_alias="STORAGE_DSN",
    )
    app_password: str = Field(default="", validation_alias="POSTGRES_APP_PASSWORD")
    redis_url: str = Field(default="redis://localhost:6389/0", validation_alias="STORAGE_REDIS_URL")
    echo_sql: bool = Field(default=False, validation_alias="STORAGE_ECHO_SQL")


def app_dsn(owner_dsn: str, password: str, role: str = APP_ROLE) -> str:
    """Адрес роли приложения: драйвер, хост, порт, база и параметры — владельца, имя и пароль —
    роли. Пароль пуст — адрес владельца как есть.

    Имя и пароль экранируются: `@`, `/` и `:` в пароле роли не ломают адрес (пароль владельца
    компоуз вставляет в `STORAGE_DSN` как есть). Разбор — `urllib`, а не `make_url` SQLAlchemy:
    настройки читает и проверка здоровья контейнера раз в минуту, а импорт SQLAlchemy — это
    почти полсекунды на каждый её запуск (замер 20.11.2026)."""
    if not password:
        return owner_dsn
    parts = urlsplit(owner_dsn)
    host = parts.netloc.rpartition("@")[2]
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


_s = _Storage()

#: Владелец схемы — только миграции и выдача прав (`migrate`), резервные копии.
OWNER_DSN: str = _s.dsn
#: Пробелы по краям — не пароль: строка из одних пробелов значит «не задан».
APP_PASSWORD: str = _s.app_password.strip()
#: Адрес, которым ходят сервисы: роль приложения, а без её пароля — владелец.
DSN: str = app_dsn(OWNER_DSN, APP_PASSWORD)
REDIS_URL: str = _s.redis_url
ECHO_SQL: bool = _s.echo_sql
