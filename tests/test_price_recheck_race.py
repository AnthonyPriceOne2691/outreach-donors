"""Два нажатия «Перезапросить цену» одного донора разом — одно письмо.

Проверка 12.10.2026: ключ письма строился из счётчика, прочитанного после проверки «писем
донору нет». Два одновременных нажатия оба видели «писем нет», второе считало номер уже
после фиксации первого — и уводило донору второе письмо с другим ключом. Теперь строка
донора захватывается до чтения (`FOR UPDATE`), и второе нажатие ждёт первое.

**Гонка настоящая** — тот же приём, что у двойной отправки (`test_send_race.py`): свои
сессии и соединения, настоящие фиксации, чистка таблиц после теста. Порядок задаёт крючок
после чтения фактов: первое ждёт, пока прочтёт второе, второе — пока первое не отдаст
письмо почте. Без захвата так и выходило два письма; с захватом второе до чтения
не доходит, пока первое не зафиксировало письмо.

Захват держится только на своём доноре: отказ отпускает строку сразу, а не в конце
пачки, — иначе две пачки с общими донорами в разном порядке ждали бы друг друга.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import price_recheck
from backend.features.letters.chain import RECHECK_STEP
from backend.features.letters.sending import Sending
from backend.features.letters.transport import Outgoing
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.recheck_world import Recording, manual_stale, stale_conversation
from tests.test_send_race import committed_sessions

Factory = async_sessionmaker[AsyncSession]

#: Сколько первый ждёт второго после чтения. Захват не пускает второго к чтению,
#: и ожидание кончается по сроку — коротким, чтобы тест не тянулся.
MEET = 1.0


@pytest.fixture
async def committed() -> AsyncIterator[Factory]:
    async with committed_sessions() as factory:
        yield factory


class Handing(Recording):
    """Почта, которая говорит, когда ей отдали первое письмо."""

    def __init__(self) -> None:
        super().__init__()
        self.first = asyncio.Event()

    async def send(self, outgoing: Outgoing) -> str:
        handed = await super().send(outgoing)
        self.first.set()
        return handed


async def _rechecks(factory: Factory) -> int:
    async with factory() as session:
        found = await session.scalar(
            select(func.count(MessageModel.id)).where(MessageModel.step == RECHECK_STEP)
        )
        return int(found or 0)


async def _lockable(factory: Factory, donor_id: int) -> bool:
    """Строку донора можно захватить сейчас: её никто не держит — захват с пропуском
    занятых (`SKIP LOCKED`) её находит."""
    async with factory() as session:
        found = await session.scalar(
            select(DonorModel.id).where(DonorModel.id == donor_id).with_for_update(skip_locked=True)
        )
        return found is not None


class TestTwoClicks:
    async def test_two_clicks_at_once_send_one_letter(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        moment = datetime.now(UTC).replace(microsecond=0)
        async with committed() as session:
            donor_id = (await stale_conversation(session, moment)).donor.id
        transport = Handing()
        looked: list[int] = []
        second_looked = asyncio.Event()
        look = price_recheck.look

        async def meet(*args: Any, **kwargs: Any) -> price_recheck.Recheck:
            found = await look(*args, **kwargs)
            looked.append(len(looked))
            if looked[-1] == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(second_looked.wait(), timeout=MEET)
            else:
                second_looked.set()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(transport.first.wait(), timeout=MEET * 3)
            return found

        monkeypatch.setattr(price_recheck, "look", meet)

        async def click() -> price_recheck.Asked | price_recheck.Refused:
            async with committed() as session:
                sending = Sending(session, transport, now=moment)
                [done] = await price_recheck.ask_many(
                    session, sending, [donor_id], author_id=None, now=moment
                )
                return done

        results = await asyncio.gather(click(), click())

        assert len(transport.seen) == 1
        assert sorted(type(one).__name__ for one in results) == ["Asked", "Refused"]
        assert await _rechecks(committed) == 1

    async def test_a_key_clash_is_said_in_words_and_the_rest_go(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Вставка упала на уникальности ключа — у этого донора отказ словами, сессия
        откатывается, остальные доноры нажатия уходят."""
        moment = datetime.now(UTC).replace(microsecond=0)
        async with committed() as session:
            clashing = (await stale_conversation(session, moment)).donor.id
            fine = (await manual_stale(session, moment, host="fine.example.test")).id
        materialize = price_recheck._materialize

        async def clash(session: AsyncSession, found: Any, plan: Any) -> MessageModel:
            if found.donor_id == clashing:
                raise IntegrityError(
                    "INSERT INTO messages", {}, Exception("duplicate key value: uq_messages")
                )
            return await materialize(session, found, plan)

        monkeypatch.setattr(price_recheck, "_materialize", clash)
        transport = Recording()

        async with committed() as session:
            done = await price_recheck.ask_many(
                session,
                Sending(session, transport, now=moment),
                [clashing, fine],
                author_id=None,
                now=moment,
            )

        refused, asked = done
        assert isinstance(refused, price_recheck.Refused)
        assert (refused.host, refused.reason) == ("stale.example.test", price_recheck.RACED)
        assert isinstance(asked, price_recheck.Asked)
        assert [out.to for out in transport.seen] == ["editor@fine.example.test"]

    async def test_a_refusal_lets_the_donor_go_before_the_next(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отказанный донор отпущен, пока пачка идёт к следующему: захваты не копятся."""
        moment = datetime.now(UTC).replace(microsecond=0)
        async with committed() as session:
            going = (await stale_conversation(session, moment)).donor.id
            fresh = await manual_stale(session, moment, host="fresh.example.test")
            fresh.last_price_at = moment
            await session.commit()
            refused = fresh.id
        look = price_recheck.look
        free: list[bool] = []

        async def probe(session: AsyncSession, donor_id: int, **kwargs: Any) -> Any:
            if donor_id == going:
                free.append(await _lockable(committed, refused))
            return await look(session, donor_id, **kwargs)

        monkeypatch.setattr(price_recheck, "look", probe)

        async with committed() as session:
            done = await price_recheck.ask_many(
                session,
                Sending(session, Recording(), now=moment),
                [refused, going],
                author_id=None,
                now=moment,
            )

        refusal, asked = done
        assert isinstance(refusal, price_recheck.Refused)
        assert "свежая" in refusal.reason
        assert isinstance(asked, price_recheck.Asked)
        assert free == [True]
