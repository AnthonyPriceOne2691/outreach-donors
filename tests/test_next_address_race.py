"""Два сборщика одновременно: второе письмо на следующий адрес — одно.

Донор открывается для первого письма заново, когда прежнее не дошло,
и в эту минуту его видят открытым все, кто собирает очередь: повтор
задачи, второе нажатие «Собрать», две рассылки подряд. Оба посчитают
один номер попытки — и защита здесь одна: уникальный ключ письма
(`этап:домен:0:a2`). Вторая вставка обязана упасть на нём, а не лечь
вторым письмом тому же донору.

**Гонка настоящая, а не изображённая** — тем же приёмом, что у двойной
отправки (`test_send_race.py`): данные фиксируются по-настоящему,
у каждого сборщика своя сессия и своё соединение, а после теста таблицы
вычищаются. Сборщики встречаются в точке, где оба уже выбрали донора
и зовут модель, — так гонка воспроизводится каждый раз. Без номера
попытки в ключе оба теста падают ещё раньше, иначе: второе первое письмо
не вставляется никогда (проверено порчей).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from backend.features.contacts.preference import DEAD
from backend.features.core.domain import ContactSource, MessageStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteResult
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.conftest import TEST_DSN, make_donor

HOST = "race.example.test"

#: Сколько ждать второго сборщика. Не дождались — один упал раньше встречи,
#: и тест должен сказать это, а не висеть.
MEETING = 5


class MeetingRewriter:
    """Модель, у которой оба сборщика встречаются: донор выбран, письма ещё нет."""

    def __init__(self, barrier: asyncio.Barrier) -> None:
        self._barrier = barrier

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        await asyncio.wait_for(self._barrier.wait(), timeout=MEETING)
        return RewriteResult(notes=["модель в тесте не участвует"])


@pytest.fixture
async def committed() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии на настоящих соединениях с настоящими фиксациями; после теста
    база вычищается целиком — остальные тесты ждут пустые таблицы."""
    engine = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ).scalars()
            listed = ", ".join(f'"{table}"' for table in tables)
            await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
        await engine.dispose()


async def _first_letter_bounced(factory: async_sessionmaker[AsyncSession]) -> None:
    """Донор с двумя адресами; письмо на первый не дошло — всё зафиксировано."""
    async with factory() as session:
        domain = await make_donor(session, HOST, email=f"info@{HOST}")
        session.add(
            ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
        )
        info = await session.scalar(
            select(ContactModel).where(ContactModel.email == f"info@{HOST}")
        )
        assert info is not None
        info.verification_status = DEAD
        campaign = CampaignModel(name="Первая", stage=Stage.DONORS)
        session.add(campaign)
        await session.flush()
        thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=info.id)
        session.add(thread)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=domain.id,
                contact_id=info.id,
                step=0,
                status=MessageStatus.BOUNCED,
                idempotency_key=f"donors:{HOST}:0",
            )
        )
        await session.commit()


async def _race(
    factory: async_sessionmaker[AsyncSession], names: tuple[str, str]
) -> list[BuildReport | BaseException]:
    barrier = asyncio.Barrier(2)

    async def build(name: str) -> BuildReport:
        async with factory() as session:
            return await QueueBuilder(session, MeetingRewriter(barrier)).build(  # type: ignore[arg-type]
                BuildRequest(campaign_name=name)
            )

    # Отказ проигравшего — значением, а не исключением: кто проиграет,
    # решает база, и проверяется, что ровно один.
    return await asyncio.gather(*(build(name) for name in names), return_exceptions=True)


async def _second_attempts(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(MessageModel.idempotency_key)
            .where(MessageModel.step == 0)
            .order_by(MessageModel.id)
        )
        return list(rows)


class TestTwoBuildersOneLetter:
    async def test_two_campaigns_at_once_insert_one_letter(
        self, committed: async_sessionmaker[AsyncSession], filled_legal: None
    ) -> None:
        """Две разные рассылки — разные диалоги, и держит только ключ письма."""
        await _first_letter_bounced(committed)

        outcomes = await _race(committed, ("Вторая-А", "Вторая-Б"))

        winners = [outcome for outcome in outcomes if isinstance(outcome, BuildReport)]
        losers = [outcome for outcome in outcomes if isinstance(outcome, IntegrityError)]
        assert len(winners) == 1, outcomes
        assert len(losers) == 1, outcomes
        assert "uq_messages_idempotency" in str(losers[0])
        assert winners[0].prepared == 1
        assert await _second_attempts(committed) == [f"donors:{HOST}:0", f"donors:{HOST}:0:a2"]

    async def test_same_campaign_twice_inserts_one_letter(
        self, committed: async_sessionmaker[AsyncSession], filled_legal: None
    ) -> None:
        """Повтор задачи или двойное нажатие — одна рассылка: второй упирается
        раньше, в диалог того же адреса, но итог тот же — письмо одно."""
        await _first_letter_bounced(committed)
        async with committed() as session:
            session.add(CampaignModel(name="Вторая", stage=Stage.DONORS))
            await session.commit()

        outcomes = await _race(committed, ("Вторая", "Вторая"))

        assert sum(isinstance(outcome, BuildReport) for outcome in outcomes) == 1, outcomes
        assert sum(isinstance(outcome, IntegrityError) for outcome in outcomes) == 1, outcomes
        assert await _second_attempts(committed) == [f"donors:{HOST}:0", f"donors:{HOST}:0:a2"]
        async with committed() as session:
            threads = await session.scalar(select(func.count()).select_from(ThreadModel))
        assert threads == 2
