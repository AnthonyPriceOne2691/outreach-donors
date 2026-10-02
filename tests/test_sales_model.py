"""Лид и гипотеза продаж на настоящей базе — срез 1.1a.

Строки `domains` и `contacts` общие с донорами, и код доноров их удаляет:
адрес — с карточки донора, домен — только выдуманный, командой
`demo-seed --clear`. Тесты держат обе половины требования: лид не исчезает
молча, а удаление у доноров не начинает падать. Ключ к адресу проверяется
с трёх сторон: выбранный `SET NULL` и две отвергнутые замены.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.contacts import manual
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.sales.models import LeadSource, SalesHypothesisModel, SalesLeadModel
from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "backend/migrations/versions/715bbf374195_sales_hypotheses_and_leads.py"
JOURNAL = ROOT / "backend/migrations/versions/1f7b0ee634c2_sales_leads_imported_audit_action.py"
TABLES = ("sales_hypotheses", "sales_leads")
TYPES = ("sales_lead_source", "sales_lead_status")
SHARED = "sales@shared.example"


async def _hypothesis(session: AsyncSession) -> SalesHypothesisModel:
    hypothesis = SalesHypothesisModel(name="проверка")
    session.add(hypothesis)
    await session.flush()
    return hypothesis


async def _domain(session: AsyncSession, host: str) -> DomainModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    return domain


def _lead(
    hypothesis: SalesHypothesisModel,
    domain: DomainModel | None,
    email: str,
    contact: ContactModel | None = None,
) -> SalesLeadModel:
    return SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=None if domain is None else domain.id,
        contact_id=None if contact is None else contact.id,
        email=email,
        source=LeadSource.IMPORT,
    )


async def _leads(session: AsyncSession) -> list[tuple[int | None, str]]:
    """Лиды прямо из базы: ссылка на адрес и сам адрес."""
    rows = await session.execute(
        select(SalesLeadModel.contact_id, SalesLeadModel.email).order_by(SalesLeadModel.id)
    )
    return [(contact_id, email) for contact_id, email in rows]


async def _shared_address(session: AsyncSession) -> tuple[DonorModel, ContactModel]:  # A6
    """Адрес, который у доноров на карточке донора и у продаж — у лида."""
    domain = await make_donor(session, "shared.example", email=SHARED)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    contact = await session.scalar(select(ContactModel).where(ContactModel.domain_id == domain.id))
    assert donor is not None
    assert contact is not None
    session.add(_lead(await _hypothesis(session), domain, SHARED, contact))
    await session.flush()
    return donor, contact


async def _contact_key_on_delete(session: AsyncSession, rule: str) -> None:
    """Подменить правило ключа к адресу — только в транзакции теста."""
    await session.execute(
        text("ALTER TABLE sales_leads DROP CONSTRAINT sales_leads_contact_id_fkey")
    )
    await session.execute(
        text(
            "ALTER TABLE sales_leads ADD CONSTRAINT sales_leads_contact_id_fkey "
            f"FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE {rule}"
        )
    )


def _migration(path: Path = MIGRATION) -> ModuleType:  # A1
    spec = importlib.util.spec_from_file_location(f"sales_migration_{path.stem}", path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _present(connection: Connection) -> set[str]:
    """Какие таблицы и типы продаж есть в базе сейчас."""
    tables = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:names)"),
        {"names": list(TABLES)},
    )
    types = connection.execute(
        text("SELECT typname FROM pg_type WHERE typname = ANY(:names)"), {"names": list(TYPES)}
    )
    return set(tables.scalars()) | set(types.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    """Откат и повторный подъём миграции на соединении теста."""
    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        after_downgrade = _present(connection)
        migration.upgrade()
    return after_downgrade, _present(connection)


def _journal_values(connection: Connection) -> list[str]:
    """Миграция журнала среза 1.3 ещё раз, в процессе: подъём сьюта идёт подпроцессом,
    и покрытие его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = _migration(JOURNAL)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_sales_import_journal_value_is_there_and_survives_a_rerun(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    assert (await connection.run_sync(_journal_values))[-1] == "sales_leads_imported"


async def test_upgrade_head_creates_both_sales_tables(session: AsyncSession) -> None:  # A1
    """Схему сьюта поднял `alembic upgrade head` на пустой базе (conftest)."""
    connection = await session.connection()
    assert await connection.run_sync(_present) == {*TABLES, *TYPES}


async def test_downgrade_drops_tables_and_types_and_upgrade_runs_again(
    session: AsyncSession,
) -> None:  # A1
    """Урок L5 соседнего проекта: таблица не уносит с собой тип перечисления,
    и миграция без `DROP TYPE` в откате работает ровно один раз — повторный
    подъём падает на «тип уже существует». Одиночный `upgrade head` этого
    не видит, поэтому здесь цикл. DDL в Postgres транзакционный: откат теста
    возвращает схему сьюта как была."""
    connection = await session.connection()
    after_downgrade, after_upgrade = await connection.run_sync(_down_and_up)
    assert after_downgrade == set()
    assert after_upgrade == {*TABLES, *TYPES}


async def test_lead_without_company_domain_is_refused(session: AsyncSession) -> None:  # A4
    """Нет ни компании, ни сайта — домена нет, и база лида не берёт."""
    hypothesis = await _hypothesis(session)
    with pytest.raises(IntegrityError, match="domain_id"):
        async with session.begin_nested():
            session.add(_lead(hypothesis, None, "ivan@gmail.com"))
    assert await _leads(session) == []


async def test_domain_without_company_name_is_enough(session: AsyncSession) -> None:  # A4
    """Положительный контроль: обязателен домен, а не название компании."""
    domain = await _domain(session, "acme.example")
    session.add(_lead(await _hypothesis(session), domain, "ivan@acme.example"))
    await session.flush()
    assert await _leads(session) == [(None, "ivan@acme.example")]


async def test_two_people_of_one_company_are_two_leads(session: AsyncSession) -> None:  # A5
    hypothesis = await _hypothesis(session)
    domain = await _domain(session, "acme.example")
    session.add_all(
        [
            _lead(hypothesis, domain, "ivan@acme.example"),
            _lead(hypothesis, domain, "maria@acme.example"),
        ]
    )
    await session.flush()
    domains = await session.scalars(select(SalesLeadModel.domain_id))
    assert list(domains) == [domain.id, domain.id]


async def test_donor_removes_shared_address_and_the_lead_stays(session: AsyncSession) -> None:  # A6
    """Настоящий поток доноров — удаление адреса с карточки донора.

    Отказ у него только для адреса с письмами или диалогом; лид продаж его
    не держит. Удаление проходит, как раньше, лид остаётся — и знает, кому
    писать: адрес у него свой, обнулена только ссылка на общую строку."""
    donor, contact = await _shared_address(session)

    removed = await manual.remove(session, donor, contact.id)

    assert removed.email == SHARED
    assert await session.get(ContactModel, contact.id) is None
    assert await _leads(session) == [(None, SHARED)]


async def test_reverse_run_cascade_would_delete_the_lead_silently(
    session: AsyncSession,
) -> None:  # A6
    """Обратный прогон: тот же сценарий при CASCADE уносит лида без следа."""
    await _contact_key_on_delete(session, "CASCADE")
    donor, contact = await _shared_address(session)

    await manual.remove(session, donor, contact.id)

    assert await _leads(session) == []


async def test_reverse_run_restrict_would_break_address_removal_for_donors(
    session: AsyncSession,
) -> None:  # A6
    """Обратный прогон: при RESTRICT удаление адреса у доноров падает ошибкой
    базы — поток доноров сломан продажами."""
    await _contact_key_on_delete(session, "RESTRICT")
    donor, contact = await _shared_address(session)

    with pytest.raises(IntegrityError, match="sales_leads"):
        async with session.begin_nested():
            await manual.remove(session, donor, contact.id)
    assert await _leads(session) == [(contact.id, SHARED)]


@pytest.mark.parametrize("target", ["domain", "hypothesis"])
async def test_domain_or_hypothesis_of_a_lead_cannot_be_deleted(
    session: AsyncSession, target: str
) -> None:  # A7
    hypothesis = await _hypothesis(session)
    domain = await _domain(session, "acme.example")
    session.add(_lead(hypothesis, domain, "ivan@acme.example"))
    await session.flush()
    statement = (
        delete(DomainModel).where(DomainModel.id == domain.id)
        if target == "domain"
        else delete(SalesHypothesisModel).where(SalesHypothesisModel.id == hypothesis.id)
    )

    with pytest.raises(IntegrityError, match="sales_leads"):
        async with session.begin_nested():
            await session.execute(statement)
    assert await session.scalar(select(func.count()).select_from(SalesLeadModel)) == 1


def test_sales_models_import_first_in_a_clean_process() -> None:
    """`core/models/__init__.py` импортирует модели продаж, а они берут
    примесь из `core/models/`. Первый импорт `sales.models` в чистом процессе —
    так начнёт будущий воркер продаж — не должен замкнуть круг."""
    done = subprocess.run(
        [sys.executable, "-c", "import backend.features.sales.models"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
