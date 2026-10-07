"""Платформа приняла письмо (202), но не вернула своего номера: это не отказ.

До 07.10.2026 такой ответ поднимал `TransportError`, и отправка возвращала
принятое письмо в очередь — следующая отправка стала бы вторым письмом тому
же человеку. Теперь это `MaybeSentError`: письмо остаётся «отправляется»,
пачка его не берёт, и исход записывает событие платформы — по нашему номеру
письма в её `custom_args`, номер платформы для этого не нужен, — а нет его —
человек в блоке «Исход неизвестен».
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.features.core.domain import MessageStatus, Stage
from backend.features.letters import batch, unknown_outcome
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.sendgrid import SendGridTransport
from backend.features.letters.sending import Sending
from backend.features.letters.transport import MaybeSentError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_sender
from tests.thread_letters import BOX, Recording, age, first_letter

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(minutes=12)


async def test_letter_stays_sending_and_the_event_settles_it(
    session: AsyncSession, filled_legal: None
) -> None:
    letter = await first_letter(session)
    await make_sender(session, BOX)
    posted: list[dict[str, object]] = []

    def accepted_without_number(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(202)

    platform = SendGridTransport(
        api_key="sg-test-key",  # pragma: allowlist secret
        allowlist=(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(accepted_without_number)),
    )

    with pytest.raises(MaybeSentError, match="не вернула его номер"):
        await Sending(session, platform).send(letter.id)
    await platform.aclose()

    await session.refresh(letter)
    assert letter.status is MessageStatus.SENDING
    assert len(posted) == 1  # принятое не повторяется
    assert posted[0]["custom_args"] == {"message_id": str(letter.id)}
    await age(session, letter, since=SINCE)
    stuck = await unknown_outcome.stuck(session, stage=Stage.DONORS, now=NOW)
    assert [row.message.id for row in stuck] == [letter.id]
    transport = Recording()
    report = await batch.send_queue(session, transport, stage=Stage.DONORS)
    assert (transport.seen, report.sent) == ([], 0)

    settled = await apply_events(
        session, [DeliveryEvent("processed", letter.id, "editor@stuck.example.test")], now=NOW
    )

    assert settled.resolved == 1
    assert letter.status is MessageStatus.SENT
    assert letter.sent_at == SINCE  # из «отправляется» — от начала передачи
