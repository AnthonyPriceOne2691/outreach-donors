"""Повтор поиска адреса с пределом: правило очереди и запись исхода.

Поиск по базе записывал обрыв, таймаут, 5xx и 429 как «адреса нет», и донор,
недоступный в минуту сбоя, ждал повтора 180 дней. Решение Anthony 01.10.2026:
у такого прохода свой исход `no_answer` с причиной; повтор — следующий
прогон, через день, через неделю; четвёртый проход без ответа — `not_found`
на обычный срок. Правило одно на все очереди (`contacts/attempts.py`).

Здесь — расписание и запись на настоящей базе. Как лестница узнаёт, что
сайт не ответил, — `test_contacts_no_answer.py`.
"""

from __future__ import annotations

import importlib.util
import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.contacts import attempts
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import ContactRepository, refusal_of
from backend.features.core.domain import ContactStatus
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl.contacts import AdvertiserContactRepository
from sqlalchemy import Connection, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _silent(host: str) -> LadderResult:
    return LadderResult(host=host, status=ContactStatus.NO_ANSWER, reason="ошибка сервера: 503")


async def _state(session: AsyncSession, host: str) -> tuple[ContactStatus | None, int, str | None]:
    row = (
        await session.execute(
            select(DonorModel.contact_status, DonorModel.contact_tries, DonorModel.contact_reason)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DomainModel.host == host)
        )
    ).one()
    return row.contact_status, row.contact_tries, row.contact_reason


class TestTheSchedule:
    async def test_three_retries_then_not_found(self, session: AsyncSession) -> None:
        """Следующий прогон, через день, через неделю — и «адреса нет»."""
        await make_donor(session, "site.com")
        queue = ContactRepository(session)
        moment = NOW
        for tries, wait in enumerate(attempts.RETRY_DELAYS, start=1):
            await queue.save([_silent("site.com")], now=moment)
            assert await _state(session, "site.com") == (
                ContactStatus.NO_ANSWER,
                tries,
                "ошибка сервера: 503",
            )
            if wait:
                early = moment + wait - timedelta(minutes=1)
                assert await queue.pending_hosts(now=early) == [], f"после {tries}-й рано"
            moment += wait
            assert await queue.pending_hosts(now=moment) == ["site.com"], f"после {tries}-й пора"
            assert (await queue.last_tries(["site.com"])) == (
                {"site.com"} if tries == attempts.MAX_RETRIES else set()
            )

        await queue.save([_silent("site.com")], now=moment)
        status, tries, reason = await _state(session, "site.com")
        assert (status, tries) == (ContactStatus.NOT_FOUND, 0)
        assert reason == "сдались после 4 проходов без ответа: ошибка сервера: 503"
        assert await queue.pending_hosts(now=moment + timedelta(days=30)) == []

    async def test_an_answer_resets_the_count(self, session: AsyncSession) -> None:
        await make_donor(session, "site.com")
        queue = ContactRepository(session)
        await queue.save([_silent("site.com")], now=NOW)
        await queue.save([_silent("site.com")], now=NOW)
        await queue.save([LadderResult(host="site.com", status=ContactStatus.NOT_FOUND)], now=NOW)
        assert await _state(session, "site.com") == (ContactStatus.NOT_FOUND, 0, None)

    async def test_quota_does_not_touch_the_count(self, session: AsyncSession) -> None:
        """Квота — «не спросили»: счёт молчания сайта она не сбрасывает."""
        await make_donor(session, "site.com")
        queue = ContactRepository(session)
        await queue.save([_silent("site.com")], now=NOW)
        await queue.save([LadderResult(host="site.com", status=ContactStatus.NO_QUOTA)], now=NOW)
        assert await _state(session, "site.com") == (
            ContactStatus.NO_QUOTA,
            1,
            "ошибка сервера: 503",
        )

    async def test_words_agree_with_the_query(self, session: AsyncSession) -> None:
        """Экран говорит «повторится не раньше …» ровно тогда, когда запрос
        донора не берёт, — на каждом шаге расписания и на его границах."""
        steps = [timedelta(0), timedelta(hours=23), timedelta(days=1), timedelta(days=6)]
        steps += [timedelta(days=7), timedelta(days=200)]
        disagreements: list[str] = []
        for index, (tries, ago) in enumerate(itertools.product(range(5), steps)):
            domain = await make_donor(session, f"state-{index}.example.test")
            await session.execute(
                update(DonorModel)
                .where(DonorModel.domain_id == domain.id)
                .values(
                    contact_status=ContactStatus.NO_ANSWER,
                    contact_tries=tries,
                    contact_attempted_at=NOW - ago,
                    contact_reason="нет ответа: обрыв или таймаут",
                )
            )
            donor = (
                await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
            ).scalar_one()
            await session.refresh(donor)
            waits = await ContactRepository(session, donor_id=donor.id).pending_count(now=NOW)
            said = refusal_of(donor, now=NOW)
            if bool(waits) != (said is None):
                disagreements.append(f"{tries} раз, {ago} назад: запрос {waits}, слова {said!r}")
        assert disagreements == []

    async def test_advertisers_follow_the_same_rule(self, session: AsyncSession) -> None:
        """Правило одно на все очереди: рекламодатель повторяется так же."""
        domain = DomainModel(host="site.com")
        session.add(domain)
        await session.flush()
        session.add(AdvertiserModel(domain_id=domain.id))
        await session.flush()
        queue = AdvertiserContactRepository(session)

        await queue.save([_silent("site.com")], now=NOW)
        await queue.save([_silent("site.com")], now=NOW)
        assert await queue.pending_hosts(now=NOW + timedelta(hours=23)) == []
        assert await queue.pending_hosts(now=NOW + timedelta(days=1)) == ["site.com"]


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "backend/migrations/versions/b3e8d1f04a62_contact_no_answer.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contact_no_answer_migration", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection: Connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE column_name IN ('contact_tries', 'contact_reason')"
        )
    )
    return {(table, column) for table, column in rows.tuples()}


def _down_and_up(connection: Connection) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Откат и подъём миграции на соединении теста — в его транзакции."""
    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        after_downgrade = _columns(connection)
        migration.upgrade()
    return after_downgrade, _columns(connection)


class TestTheMigration:
    async def test_down_and_up_again(self, session: AsyncSession) -> None:
        """Откат снимает счёт и причину у обеих очередей, подъём возвращает.

        Значение `no_answer` откат оставляет: из перечисления Postgres его
        не убрать без пересоздания типа (см. докстринг отката).
        """
        connection = await session.connection()
        after_downgrade, after_upgrade = await connection.run_sync(_down_and_up)
        expected = {
            (table, column)
            for table in ("donors", "advertisers")
            for column in ("contact_tries", "contact_reason")
        }
        assert after_downgrade.isdisjoint(expected)
        assert expected <= after_upgrade
        labels = await session.scalars(text("SELECT unnest(enum_range(NULL::contactstatus))::text"))
        assert "no_answer" in set(labels)
