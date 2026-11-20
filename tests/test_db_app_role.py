"""Роль приложения в базе: что ей можно и что нельзя (аудит 20.11.2026, п. 31).

`shared/database/app_role.py` заводит роль и выдаёт права — здесь на настоящей базе тестов,
ролью со своим именем на каждый тест (роль — объект кластера, а процессы xdist и деревья
делят один сервер). Вопросы теста:
- роль читает и пишет строки разных таблиц и берёт номера последовательностей — так ходят
  сервер и воркеры;
- DDL, функции администратора, обход триггеров и правка журнала действий — отказ `42501`;
- таблица, которую заведёт следующая миграция, получает те же права без нового запуска шага;
- повтор шага возвращает роль к задуманному виду, новый пароль заменяет старый;
- без пароля роли сервисы ходят владельцем, как до 20.11.2026, а шаг не трогает базу;
- пароль не печатается — ни открытым текстом, ни проверочной строкой.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from backend.config import storage
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.shared.database import app_role
from sqlalchemy import delete, func, make_url, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from tests.conftest import TEST_DSN, make_donor

ROOT = Path(__file__).resolve().parent.parent
#: Команда контейнера `migrate`: сначала схема, затем роль приложения и её права.
MIGRATE = ["sh", "-c", "alembic upgrade head && python -m backend.shared.database.app_role"]
REFUSED = "42501"
#: Адрес, по которому не ответит никто: шаг без пароля не обязан туда ходить.
NOWHERE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nowhere"  # pragma: allowlist secret
#: Адрес владельца, как его собирает компоуз, — с выдуманным паролем.
OWNER = "postgresql+asyncpg://outreach:owner@postgres:5432/outreach"  # pragma: allowlist secret
#: Номер последовательности доменов — так его берёт вставка строки.
NEXT_DOMAIN = "SELECT nextval(pg_get_serial_sequence('domains', 'id'))"
THIS_DOMAIN = "SELECT currval(pg_get_serial_sequence('domains', 'id'))"


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    password: str

    @property
    def dsn(self) -> str:
        return storage.app_dsn(TEST_DSN, self.password, self.name)


def _engine(dsn: str) -> AsyncEngine:
    return create_async_engine(dsn, poolclass=NullPool)


async def _as_owner(*statements: str) -> None:
    """Команды владельцем схемы — с фиксацией."""
    owner = _engine(TEST_DSN)
    try:
        async with owner.begin() as conn:
            for statement in statements:
                await conn.exec_driver_sql(statement)
    finally:
        await owner.dispose()


async def _ensure(name: str, password: str) -> None:
    owner = _engine(TEST_DSN)
    try:
        async with owner.begin() as conn:
            await app_role.ensure(conn, role=name, password=password)
    finally:
        await owner.dispose()


async def _drop(name: str) -> None:
    """Роль и всё, что ей выдано в этой базе, — прочь. Роли нет — нечего и сносить."""
    owner = _engine(TEST_DSN)
    try:
        async with owner.begin() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name}
            )
            if found:
                await conn.exec_driver_sql(f'DROP OWNED BY "{name}"')
                await conn.exec_driver_sql(f'DROP ROLE "{name}"')
    finally:
        await owner.dispose()


def _fresh_name() -> str:
    return f"outreach_app_t{secrets.token_hex(4)}"


@pytest.fixture
async def role() -> AsyncIterator[Role]:
    made = Role(_fresh_name(), secrets.token_hex(16))
    try:
        await _ensure(made.name, made.password)
        yield made
    finally:
        await _drop(made.name)


@pytest.fixture
async def as_role(role: Role) -> AsyncIterator[AsyncEngine]:
    engine = _engine(role.dsn)
    yield engine
    await engine.dispose()


async def _refusal(engine: AsyncEngine, statement: str) -> str:
    """Код отказа базы на `statement`; прошло — тест падает. Транзакция откатывается всегда:
    прошедший `DROP TABLE` не должен остаться в базе тестов."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            with pytest.raises(DBAPIError) as caught:
                await conn.exec_driver_sql(statement)
        finally:
            await transaction.rollback()
    return str(getattr(caught.value.orig, "sqlstate", None))


async def _logs_in(dsn: str) -> bool:
    engine = _engine(dsn)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


async def _login_refusal(dsn: str) -> str:
    """Код отказа входа. Отказ входа asyncpg отдаёт мимо SQLAlchemy — своим исключением."""
    with pytest.raises(asyncpg.PostgresError) as caught:
        await _logs_in(dsn)
    return str(caught.value.sqlstate)


class TestWhatTheRoleDoes:
    async def test_rows_of_several_tables_are_read_and_written(self, as_role: AsyncEngine) -> None:
        async with as_role.connect() as conn:
            transaction = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                host = "role.example.test"
                domain = await make_donor(session, host, email=f"editor@{host}")
                author = await AccessRepository(session).create(
                    email="роль@team.example.test", password="пароль-теста", role=UserRole.ADMIN
                )
                journal = AccessRepository(session)
                await journal.record(AuditAction.RUN_STARTED, author_id=author.id, target="run:1")
                await session.execute(
                    update(DonorModel).where(DonorModel.domain_id == domain.id).values(dr=55)
                )
                await session.execute(
                    delete(ContactModel).where(ContactModel.domain_id == domain.id)
                )
                await session.flush()

                assert await session.scalar(select(DonorModel.dr)) == 55
                assert await session.scalar(select(func.count()).select_from(ContactModel)) == 0
                started = "SELECT count(*) FROM audit_log WHERE target = 'run:1'"
                assert await session.scalar(text(started)) == 1
                taken = await session.scalar(text(NEXT_DOMAIN))
                assert await session.scalar(text(THIS_DOMAIN)) == taken
            finally:
                await session.close()
                await transaction.rollback()

    @pytest.mark.parametrize(
        "statement",
        [
            "CREATE TABLE public.probe (id integer)",
            "CREATE FUNCTION public.probe() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
            "CREATE SCHEMA probe",
            "ALTER TABLE domains ADD COLUMN probe integer",
            "CREATE INDEX probe_idx ON domains (host)",
            "DROP TABLE domains",
            "TRUNCATE domains",
            "CREATE ROLE probe_role",
            "ALTER ROLE CURRENT_USER SUPERUSER",
            "COPY domains TO PROGRAM 'id'",
            "SELECT pg_read_file('PG_VERSION')",
            "SET session_replication_role = replica",
            "UPDATE audit_log SET target = 'правка'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
            "UPDATE alembic_version SET version_num = 'probe'",
        ],
        ids=[
            "таблица",
            "функция",
            "схема",
            "колонка",
            "индекс",
            "снос таблицы",
            "очистка таблицы",
            "роль",
            "суперправа себе",
            "программа на машине базы",
            "файл машины базы",
            "обход триггеров",
            "правка журнала",
            "удаление журнала",
            "очистка журнала",
            "отметка миграций",
        ],
    )
    async def test_ddl_and_admin_are_refused(self, as_role: AsyncEngine, statement: str) -> None:
        assert await _refusal(as_role, statement) == REFUSED

    async def test_tables_of_later_migrations_come_with_the_same_rights(
        self, as_role: AsyncEngine
    ) -> None:
        """`ALTER DEFAULT PRIVILEGES` владельца: таблица заведена после шага выдачи прав."""
        table = f"probe_{secrets.token_hex(4)}"
        await _as_owner(f"CREATE TABLE public.{table} (id serial PRIMARY KEY, note text)")
        try:
            async with as_role.connect() as conn:
                transaction = await conn.begin()
                try:
                    made = await conn.scalar(
                        text(f"INSERT INTO {table} (note) VALUES ('новая') RETURNING id")
                    )
                    await conn.execute(
                        text(f"UPDATE {table} SET note = 'правка' WHERE id = :id"), {"id": made}
                    )
                    assert await conn.scalar(text(f"SELECT note FROM {table}")) == "правка"
                    await conn.execute(text(f"DELETE FROM {table}"))
                finally:
                    await transaction.rollback()
        finally:
            await _as_owner(f"DROP TABLE public.{table}")


class TestTheStep:
    async def test_a_repeat_resets_what_was_granted_by_hand(self, role: Role) -> None:
        await _as_owner(
            f'ALTER ROLE "{role.name}" CREATEDB',
            f'GRANT TRUNCATE ON domains TO "{role.name}"',
            f'GRANT CREATE ON SCHEMA public TO "{role.name}"',
        )

        await _ensure(role.name, role.password)

        owner = _engine(TEST_DSN)
        try:
            async with owner.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
                            "rolcanlogin, rolreplication, rolbypassrls, "
                            "has_table_privilege(rolname, 'domains', 'TRUNCATE'), "
                            "has_schema_privilege(rolname, 'public', 'CREATE') "
                            "FROM pg_roles WHERE rolname = :name"
                        ),
                        {"name": role.name},
                    )
                ).one()
        finally:
            await owner.dispose()
        assert tuple(row) == (False, False, False, False, True, False, False, False, False)
        assert await _logs_in(role.dsn)

    async def test_a_new_password_replaces_the_old_one(self, role: Role) -> None:
        renewed = Role(role.name, secrets.token_hex(16))

        await _ensure(renewed.name, renewed.password)

        assert await _login_refusal(role.dsn) == "28P01"
        assert await _logs_in(renewed.dsn)

    async def test_the_check_is_silent_for_the_role_and_names_the_owner(
        self, role: Role, as_role: AsyncEngine
    ) -> None:
        async with as_role.connect() as conn:
            assert await app_role.problems(conn, role.name) == []
        owner = _engine(TEST_DSN)
        try:
            async with owner.connect() as conn:
                said = await app_role.problems(conn, role.name)
        finally:
            await owner.dispose()
        assert any("суперправа" in line for line in said)
        assert any("журнал действий" in line for line in said)

    @pytest.mark.parametrize("password", ["пароль", "tab\there"], ids=["кириллица", "табуляция"])
    def test_only_printable_ascii_is_hashed(self, password: str) -> None:
        """Иначе нормализация пароля сервером (SASLprep) и строка, посчитанная здесь,
        разошлись бы при входе."""
        with pytest.raises(app_role.AppRoleError, match="openssl rand -hex 24"):
            app_role.scram_verifier(password)

    async def test_a_name_outside_the_rule_is_refused_before_the_base(self) -> None:
        owner = _engine(TEST_DSN)
        try:
            async with owner.begin() as conn:
                with pytest.raises(app_role.AppRoleError, match="имя роли"):
                    await app_role.ensure(conn, role='x"; DROP TABLE domains; --', password="abc")
        finally:
            await owner.dispose()


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    """Текст записей журнала вместе с полями `extra`: `caplog.text` полей не печатает."""
    return "\n".join(str(vars(record)) for record in caplog.records)


class TestTheEntryPoint:
    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app_role, "setup_logging", lambda: None)  # журнал — в caplog

    def test_without_a_password_the_base_is_not_touched(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(storage, "APP_PASSWORD", "")
        monkeypatch.setattr(storage, "OWNER_DSN", NOWHERE)
        caplog.set_level(logging.INFO, logger=app_role.__name__)

        assert app_role.main([]) == 0
        assert "роль не трогается" in caplog.text

    def test_the_step_grants_and_never_says_the_password(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        name, password = _fresh_name(), secrets.token_hex(16)
        monkeypatch.setattr(storage, "APP_ROLE", name)
        monkeypatch.setattr(storage, "APP_PASSWORD", password)
        monkeypatch.setattr(storage, "OWNER_DSN", TEST_DSN)
        monkeypatch.setattr(storage, "DSN", storage.app_dsn(TEST_DSN, password, name))
        caplog.set_level(logging.DEBUG)
        try:
            assert app_role.main([]) == 0
            assert app_role.main(["--check"]) == 0
        finally:
            asyncio.run(_drop(name))

        printed = capsys.readouterr()
        said = _everything_logged(caplog) + printed.out + printed.err
        assert f"Сервис ходит ролью «{name}»" in printed.out
        assert password not in said
        assert "SCRAM-SHA-256" not in said

    def test_a_password_the_driver_would_hash_differently_stops_the_step_in_words(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Код 1 — `migrate` не завершился успехом, и сервисы не стартуют с паролем, которым не
        войти; в журнале — что не так, без самого пароля."""
        name = _fresh_name()
        monkeypatch.setattr(storage, "APP_ROLE", name)
        monkeypatch.setattr(storage, "APP_PASSWORD", "пароль-кириллицей")
        monkeypatch.setattr(storage, "OWNER_DSN", TEST_DSN)
        caplog.set_level(logging.INFO, logger=app_role.__name__)
        try:
            assert app_role.main([]) == 1
        finally:
            asyncio.run(_drop(name))

        [said] = [r for r in caplog.records if r.getMessage() == "роль приложения не заведена"]
        assert "openssl rand -hex 24" in getattr(said, "error", "")
        assert "пароль-кириллицей" not in _everything_logged(caplog)

    def test_the_check_says_when_the_services_go_as_the_owner(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)

        assert app_role.main(["--check"]) == 1
        assert "не так, как задумано" in capsys.readouterr().out

    def test_an_unknown_argument_is_refused_in_words(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert app_role.main(["--please"]) == 2
        assert "--check" in capsys.readouterr().out


class TestTheAddress:
    def test_the_role_keeps_the_owner_host_base_and_parameters(self) -> None:
        url = make_url(storage.app_dsn(f"{OWNER}?ssl=disable", "p@ss/w:rd% x"))

        assert (url.username, url.password) == (storage.APP_ROLE, "p@ss/w:rd% x")
        assert (url.drivername, url.host, url.port, url.database) == (
            "postgresql+asyncpg",
            "postgres",
            5432,
            "outreach",
        )
        assert dict(url.query) == {"ssl": "disable"}

    def test_without_a_password_it_is_the_owner_address(self) -> None:
        assert storage.app_dsn(OWNER, "") == OWNER


def _compose() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    return loaded


class TestTheDeploy:
    def test_migrate_grants_after_the_schema_and_ci_runs_the_same(self) -> None:
        """CI проверяет образ той же командой, какой его запускает сервер."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        assert _compose()["services"]["migrate"]["command"] == MIGRATE
        assert f'sh -c "{MIGRATE[2]}"' in ci
        assert "python -m backend.shared.database.app_role --check" in ci

    def test_every_backend_service_gets_the_owner_address_and_the_role_password(self) -> None:
        """Пароль роли — всем из образа сервера: сервисам — ходить ролью, `migrate` — завести её.
        Пусто по умолчанию: без него компоуз стартует, и сервисы ходят владельцем."""
        backend = {
            name: service
            for name, service in _compose()["services"].items()
            if str(service.get("image", "")).startswith("${BACKEND_IMAGE")
        }

        assert {"migrate", "api", "worker", "crawler", "reaper", "followups"} <= set(backend)
        for name, service in backend.items():
            environment = service["environment"]
            assert environment["POSTGRES_APP_PASSWORD"] == "${POSTGRES_APP_PASSWORD:-}", name
            assert environment["STORAGE_DSN"].startswith("postgresql+asyncpg://outreach:"), name
