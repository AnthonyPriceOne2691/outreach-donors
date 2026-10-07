"""Событие о письме, возвращённом в очередь, и пачка, которая его берёт, — разом.

Человек не нашёл письмо в журнале платформы и вернул его в очередь, а оно ушло.
Событие платформы снимает его с очереди (`unknown_outcome.settle_by_event`),
пачка в ту же минуту его захватывает (`Sending._claim`) — и то и другое
условный `UPDATE … WHERE status = 'queued'`. Кто-то из двоих видит «уже не
в очереди»: либо пачка не отдаёт письмо почте, либо событие опоздало
и второго письма уже не вернуть, но запись «ушло» об одном письме — одна.

**Гонка настоящая** — тот же приём, что у двойной отправки (`test_send_race.py`):
свои сессии и соединения, настоящие фиксации, чистка таблиц после теста.
Порядок задаётся крючком в точке записи; последний тест — без порядка.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import batch, mailbox, settle, unknown_outcome
from backend.features.letters.batch import BatchReport
from backend.features.letters.events import EventReport
from backend.features.letters.sending import Sending
from backend.features.letters.unknown_outcome import Outcome
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_letters_unknown_race import _event, _letter, _sent_records, _spent
from tests.test_send_race import MEETING, committed_sessions
from tests.thread_letters import Recording, stuck_first

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(minutes=12)

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def committed() -> AsyncIterator[Factory]:
    async with committed_sessions() as factory:
        yield factory


async def _requeued(factory: Factory) -> int:
    """Письмо, на котором оборвалась связь, — человек вернул его в очередь."""
    async with factory() as session:
        letter = await stuck_first(session, since=SINCE)
        await unknown_outcome.resolve(session, letter.id, Outcome.QUEUED, author_id=None, now=NOW)
        await session.commit()
        return letter.id


async def _batch(factory: Factory, transport: Recording) -> BatchReport:
    async with factory() as session:
        return await batch.send_queue(session, transport, stage=Stage.DONORS)


class TestEventAndBatch:
    async def test_event_lands_while_the_batch_holds_the_letter(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пачка прочла письмо «в очереди» и выбрала ящик — и тут событие снимает
        его с очереди. Захват пачки видит «уже не в очереди»: почте письмо
        не отдаётся, а итог пачки называет это словами."""
        letter_id = await _requeued(committed)
        reports: list[EventReport] = []
        choose = mailbox.choose

        async def event_in_between(*args: Any, **kwargs: Any) -> mailbox.Choice:
            choice = await choose(*args, **kwargs)
            reports.append(await _event(committed, letter_id))
            return choice

        monkeypatch.setattr(mailbox, "choose", event_in_between)
        transport = Recording()

        report = await _batch(committed, transport)

        assert transport.seen == []
        assert (report.sent, report.refused) == (0, {"уже не в очереди": 1})
        assert reports[0].resolved == 1
        records = await _sent_records(committed)
        assert [record["как узнали"] for record in records] == [
            "событие платформы «processed» после возврата в очередь"
        ]
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT

    async def test_batch_first_the_late_event_writes_nothing_of_its_own(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Событие прочло письмо «в очереди», а пачка успела раньше: отправила
        и записала. Второе письмо адресату уже ушло — событие опоздало, — но
        второй записи «ушло» нет."""
        letter_id = await _requeued(committed)
        transport = Recording()
        original = settle.leave
        landed = False

        async def batch_first(session: AsyncSession, message: MessageModel, **values: Any) -> bool:
            nonlocal landed
            if not landed:
                landed = True
                assert values["was"] is MessageStatus.QUEUED
                assert (await _batch(committed, transport)).sent == 1
            return await original(session, message, **values)

        monkeypatch.setattr(settle, "leave", batch_first)

        report = await _event(committed, letter_id)

        assert [out.message_id for out in transport.seen] == [letter_id]
        assert report.resolved == 0
        records = await _sent_records(committed)
        assert len(records) == 1
        assert "как узнали" not in records[0]  # записал ответ платформы на повтор
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT

    async def test_at_the_same_moment_the_base_decides_once(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Без порядка: событие и пачка прочли «в очереди» и встретились перед
        записью. Решает база: второй `UPDATE` ждёт блокировку строки и видит
        «уже не в очереди». Запись «ушло» — одна, письмо почте — не больше
        одного раза, и только если пачка успела первой."""
        letter_id = await _requeued(committed)
        barrier = asyncio.Barrier(2)
        leave, claim = settle.leave, Sending._claim
        met = {"event": False, "batch": False}

        async def event_meets(session: AsyncSession, message: MessageModel, **values: Any) -> bool:
            if values["was"] is MessageStatus.QUEUED and not met["event"]:
                met["event"] = True
                await asyncio.wait_for(barrier.wait(), timeout=MEETING)
            return await leave(session, message, **values)

        async def batch_meets(self: Sending, *args: Any) -> None:
            if not met["batch"]:
                met["batch"] = True
                await asyncio.wait_for(barrier.wait(), timeout=MEETING)
            await claim(self, *args)

        monkeypatch.setattr(settle, "leave", event_meets)
        monkeypatch.setattr(Sending, "_claim", batch_meets)
        transport = Recording()

        event, sent = await asyncio.gather(
            _event(committed, letter_id), _batch(committed, transport)
        )

        assert met == {"event": True, "batch": True}
        if transport.seen:
            assert [out.message_id for out in transport.seen] == [letter_id]
            assert sent.sent == 1
        else:
            assert (sent.sent, sent.refused) == (0, {"уже не в очереди": 1})
            assert event.resolved == 1
        assert len(await _sent_records(committed)) == 1
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT
