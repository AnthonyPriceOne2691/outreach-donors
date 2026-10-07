"""Исход неизвестного письма решают два пути разом — запись «ушло» одна.

Письмо в «отправляется» выходит оттуда тремя путями (`letters/settle.py`):
ответом платформы на отправку, её событием, решением человека. Событие может
прийти раньше ответа на отправку, человек — нажать вместе с событием. Второй
путь обязан увидеть «уже не отправляется» и не писать ничего: вторая запись
«ушло» — это второй расход, вторая строка журнала и сдвинутый срок добивки.

**Гонка здесь настоящая, а не изображённая** — тот же приём, что у двойной
отправки (`test_send_race.py`): у каждого пути своя сессия и своё соединение,
данные зафиксированы по-настоящему, после теста таблицы вычищаются. Порядок
задаётся крючком в точке записи: первый путь прочёл письмо «отправляется»,
и ровно тогда второй проходит целиком и фиксируется. Последний тест —
без порядка: оба пути встречаются перед записью, и решает база.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import settle, unknown_outcome
from backend.features.letters.events import DeliveryEvent, EventReport, apply_events
from backend.features.letters.sending import Sending
from backend.features.letters.transport import Outgoing
from backend.features.letters.unknown_outcome import Outcome, ResolveError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import make_sender
from tests.test_send_race import MEETING, committed_sessions
from tests.thread_letters import BOX, first_letter, stuck_first

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(minutes=12)
TO = "editor@stuck.example.test"

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def committed() -> AsyncIterator[Factory]:
    async with committed_sessions() as factory:
        yield factory


async def _stuck(factory: Factory) -> int:
    """Письмо, на котором почта не ответила, — зафиксировано, передано 12 минут назад."""
    async with factory() as session:
        letter = await stuck_first(session, since=SINCE)
        return letter.id


async def _letter(factory: Factory, letter_id: int) -> MessageModel:
    async with factory() as session:
        letter = await session.get(MessageModel, letter_id)
        assert letter is not None
        return letter


async def _sent_records(factory: Factory) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = await session.execute(
            select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.LETTER_SENT)
        )
        return [details or {} for details in rows.scalars().all()]


async def _spent(factory: Factory) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(UsageRecordModel)
                .where(UsageRecordModel.operation == "letter_send")
            )
            or 0
        )


async def _event(factory: Factory, letter_id: int, kind: str = "processed") -> EventReport:
    """Событие платформы — тем путём, каким его применяет ручка: пачка и фиксация."""
    async with factory() as session:
        report = await apply_events(session, [DeliveryEvent(kind, letter_id, TO)], now=NOW)
        await session.commit()
        return report


async def _decide(factory: Factory, letter_id: int, outcome: Outcome) -> MessageStatus:
    """Решение человека — тем путём, каким его применяет маршрут: решение и фиксация."""
    async with factory() as session:
        done = await unknown_outcome.resolve(session, letter_id, outcome, author_id=None, now=NOW)
        await session.commit()
        return done.status


def _second_lands_first(monkeypatch: pytest.MonkeyPatch, second: Any) -> None:
    """Первый путь прочёл письмо «отправляется» и идёт записывать — а второй
    в эту минуту проходит целиком и фиксируется. Крючок — в точке записи,
    общей для всех путей (`settle.leave`)."""
    original = settle.leave
    landed = False

    async def hook(session: AsyncSession, message: MessageModel, **values: Any) -> bool:
        nonlocal landed
        if not landed:
            landed = True
            await second()
        return await original(session, message, **values)

    monkeypatch.setattr(settle, "leave", hook)


class TestEventAndHuman:
    async def test_event_first_the_human_is_refused(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        letter_id = await _stuck(committed)
        reports: list[EventReport] = []

        async def event() -> None:
            reports.append(await _event(committed, letter_id))

        _second_lands_first(monkeypatch, event)

        with pytest.raises(ResolveError, match="другим путём"):
            await _decide(committed, letter_id, Outcome.SENT)

        assert reports[0].resolved == 1
        records = await _sent_records(committed)
        assert [record["как узнали"] for record in records] == ["событие платформы «processed»"]
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT

    async def test_human_first_the_event_writes_nothing_of_its_own(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        letter_id = await _stuck(committed)

        async def human() -> None:
            await _decide(committed, letter_id, Outcome.SENT)

        _second_lands_first(monkeypatch, human)

        report = await _event(committed, letter_id, kind="delivered")

        # Второй записи «ушло» нет, а своё событие сделало: письмо дошло.
        assert report.resolved == 0
        assert report.delivered == 1
        records = await _sent_records(committed)
        assert [record["как узнали"] for record in records] == [
            "человек нашёл письмо в журнале платформы"
        ]
        assert await _spent(committed) == 1
        letter = await _letter(committed, letter_id)
        assert letter.status is MessageStatus.DELIVERED
        assert letter.sent_at == SINCE

    async def test_back_to_queue_first_the_late_event_takes_it_off_the_queue(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Человек не нашёл письма в журнале и вернул его в очередь, пока событие
        о нём уже шло. Событие проигрывает захват из «отправляется» — и записывает
        письмо ушедшим уже из очереди: оно ушло, и пачка не должна слать его
        второй раз. Запись «ушло» одна, а решение человека остаётся в журнале."""
        letter_id = await _stuck(committed)
        statuses: list[MessageStatus] = []

        async def human() -> None:
            statuses.append(await _decide(committed, letter_id, Outcome.QUEUED))

        _second_lands_first(monkeypatch, human)

        report = await _event(committed, letter_id)

        assert statuses == [MessageStatus.QUEUED]
        assert report.resolved == 1
        records = await _sent_records(committed)
        assert [record["как узнали"] for record in records] == [
            "событие платформы «processed» после возврата в очередь"
        ]
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT

    async def test_at_the_same_moment_the_base_decides_once(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Без порядка: оба прочли «отправляется» и встретились перед записью.
        Второй `UPDATE` ждёт блокировку строки и после фиксации первого видит
        «уже не отправляется». Без условия в записи «ушло» легло бы дважды."""
        letter_id = await _stuck(committed)
        barrier = asyncio.Barrier(2)
        original = settle.leave

        async def meet(session: AsyncSession, message: MessageModel, **values: Any) -> bool:
            await asyncio.wait_for(barrier.wait(), timeout=MEETING)
            return await original(session, message, **values)

        monkeypatch.setattr(settle, "leave", meet)

        event, human = await asyncio.gather(
            _event(committed, letter_id),
            _decide(committed, letter_id, Outcome.SENT),
            return_exceptions=True,
        )

        assert isinstance(event, EventReport), event
        human_won = human is MessageStatus.SENT
        assert human_won or isinstance(human, ResolveError), human
        assert event.resolved == (0 if human_won else 1)
        assert len(await _sent_records(committed)) == 1
        assert await _spent(committed) == 1
        assert (await _letter(committed, letter_id)).status is MessageStatus.SENT


class TestEventAndPlatformAnswer:
    async def test_event_lands_while_the_platform_answers(
        self, committed: Factory, filled_legal: None
    ) -> None:
        """Платформа приняла письмо и прислала событие раньше, чем дошёл её ответ
        на отправку. Ответ пишет только номер письма у платформы — его знает он
        один, — а «ушло» уже записано событием, от начала передачи."""
        async with committed() as session:
            letter_id = (await first_letter(session)).id
            await make_sender(session, BOX)
            await session.commit()

        class EventInFlight:
            name = "sendgrid"
            real = True
            claimed_at: datetime | None = None
            report: EventReport | None = None

            async def send(self, outgoing: Outgoing) -> str:
                async with committed() as other:
                    self.claimed_at = await other.scalar(
                        select(MessageModel.updated_at).where(
                            MessageModel.id == outgoing.message_id
                        )
                    )
                self.report = await _event(committed, outgoing.message_id)
                return "sg-1"

        transport = EventInFlight()
        async with committed() as session:
            outcome = await Sending(session, transport, now=NOW).send(letter_id)

        assert outcome.provider_message_id == "sg-1"
        assert transport.report is not None
        assert transport.report.resolved == 1
        records = await _sent_records(committed)
        assert [record["как узнали"] for record in records] == ["событие платформы «processed»"]
        assert await _spent(committed) == 1
        letter = await _letter(committed, letter_id)
        assert letter.status is MessageStatus.SENT
        assert letter.provider_message_id == "sg-1"
        assert letter.sent_at == transport.claimed_at

    async def test_platform_answers_while_the_event_reads(
        self, committed: Factory, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Событие прочло письмо «отправляется», а ответ платформы записал его
        ушедшим раньше: событие второй записи не делает."""
        async with committed() as session:
            letter_id = (await first_letter(session)).id
            await make_sender(session, BOX)
            await session.commit()
        claimed, read, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = unknown_outcome.settle_by_event

        async def read_then_wait(
            session: AsyncSession, message: MessageModel, *, kind: str, at: datetime
        ) -> bool:
            read.set()
            await asyncio.wait_for(sent.wait(), timeout=MEETING)
            return await original(session, message, kind=kind, at=at)

        monkeypatch.setattr(unknown_outcome, "settle_by_event", read_then_wait)

        class AnswersAfterTheRead:
            name = "sendgrid"
            real = True

            async def send(self, outgoing: Outgoing) -> str:
                claimed.set()
                await asyncio.wait_for(read.wait(), timeout=MEETING)
                return "sg-1"

        async def platform() -> str:
            async with committed() as session:
                outcome = await Sending(session, AnswersAfterTheRead(), now=NOW).send(letter_id)
            sent.set()
            return outcome.provider_message_id

        async def event() -> EventReport:
            await asyncio.wait_for(claimed.wait(), timeout=MEETING)
            return await _event(committed, letter_id)

        provider_id, report = await asyncio.gather(platform(), event())

        assert provider_id == "sg-1"
        assert report.resolved == 0
        records = await _sent_records(committed)
        assert len(records) == 1
        assert "как узнали" not in records[0]  # записал ответ платформы, а не событие
        assert await _spent(committed) == 1
