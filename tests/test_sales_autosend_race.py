"""Гонка автоотправки с кнопкой: проход и пачка разом на одно письмо — одна отправка.

Гонку закрывает тот же захват письма, что у двух нажатий (`Sending._claim`, `test_send_race.py`):
проход идёт общей отправкой, своего пути у него нет. **Гонка настоящая**: у прохода и у пачки
свои сессии и соединения, данные зафиксированы по-настоящему, после теста таблицы вычищаются.
Оба сводятся в одну точку перед захватом (`asyncio.Barrier`) — оба прочли письмо «в очереди»
и выбрали ящик, — так гонка воспроизводится каждый раз. Без захвата почта получила бы письмо
дважды.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import sending
from backend.features.letters.batch import send_queue
from backend.features.sales import autosend, queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_send_race import MEETING, committed_sessions

JANE = "jane@acme.example.test"


async def _one_sales_letter(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> int:
    """Подключённые продажи и одно письмо в очереди — зафиксированы."""
    async with factory() as session:
        world = await w.world(session, monkeypatch)
        await w.lead(session, world.hypothesis_id, JANE)
        await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
        await session.commit()
        return int(await session.scalar(select(MessageModel.id)) or 0)


def _meet_before_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оба прочли письмо и выбрали ящик — только теперь захватывают."""
    barrier = asyncio.Barrier(2)
    claim = sending.Sending._claim

    async def meet(self: sending.Sending, *args: Any, **kwargs: Any) -> None:
        await asyncio.wait_for(barrier.wait(), timeout=MEETING)
        await claim(self, *args, **kwargs)

    monkeypatch.setattr(sending.Sending, "_claim", meet)


async def test_autosend_and_the_batch_at_once_send_the_letter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with committed_sessions() as factory:
        letter_id = await _one_sales_letter(factory, monkeypatch)
        mail = Recording()
        transports = _ByStage(sales=mail, donors=Recording(), advertisers=Recording())
        _meet_before_claim(monkeypatch)

        async def by_schedule() -> autosend.Pass:
            async with factory() as session:
                return await autosend.run(session, transports, now=w.NOW)

        async def by_button() -> int:
            async with factory() as session:
                report = await send_queue(session, transports, stage=Stage.SALES)
                return report.sent

        found, batch_sent = await asyncio.gather(by_schedule(), by_button())

        assert [outgoing.message_id for outgoing in mail.seen] == [letter_id]
        # Одна отправка — у одного из двух; второму — «уже не в очереди», а не письмо.
        assert len(found.sent) + batch_sent == 1
        if not found.sent:
            assert found.refused == {letter_id: "уже не в очереди"}
        async with factory() as session:
            letter = await session.get(MessageModel, letter_id)
            assert letter is not None
            assert letter.status is MessageStatus.SENT
