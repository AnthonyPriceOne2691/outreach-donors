"""«Взять» и «В отказ» по одному лиду почти разом — под замком строки ответа (ревью 25.11.2026).

`ReplyRepository.open_lead` читал ответ без замка: оба нажатия проходили проверку и оба писали.
«Взять», закоммиченное вторым, оставляло «в отказе: отказал <взявший>» и задачу CRM, которую
передача потом отбросит. Теперь проверка — под `SELECT … FOR UPDATE`: второе нажатие ждёт коммита
первого, читает свежую строку и получает отказ словами. Та же защита закрывает два «взять» разом.

Сессии настоящие, на своих соединениях (`committed_sessions`): замок держит транзакция, а внешняя
транзакция обычного теста сделала бы обе сессии одной.
"""

from __future__ import annotations

import asyncio

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ReplyDecision
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import lead_refusal
from backend.features.replies.pipeline import Inbox
from backend.features.replies.repository import LeadError, ReplyRepository
from tests.committed_db import committed_sessions
from tests.test_letters_advertisers import NOW, SECRET, answer_to, sent_offer

#: Сколько второе нажатие ждёт замка, прежде чем тест скажет, что оно не ждёт.
HELD = 0.3
REFUSER = "anna@ours.example.test"


@pytest.mark.usefixtures("filled_legal")
async def test_a_take_waits_for_a_refusal_of_the_same_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    async with committed_sessions() as factory:
        async with factory() as session:
            letter = await sent_offer(session)
            got = await Inbox(session, now=NOW).accept(answer_to(letter, "Thanks, but no."))
            await session.commit()
        assert got.reply_id is not None
        reply_id = got.reply_id
        gate = asyncio.Event()

        async def refuse() -> None:
            async with factory() as session:
                repository = ReplyRepository(session)
                reply = await repository.reply(reply_id)
                await lead_refusal.refuse(repository, reply, by=REFUSER, now=NOW)
                await gate.wait()  # замок строки держит эта транзакция
                await session.commit()

        async def take() -> None:
            async with factory() as session:
                repository = ReplyRepository(session)
                reply = await repository.reply(reply_id)
                await repository.take_lead(reply, by="boris@ours.example.test", now=NOW)
                await session.commit()

        first = asyncio.create_task(refuse())
        second: asyncio.Task[None] | None = None
        try:
            await asyncio.sleep(HELD)
            second = asyncio.create_task(take())
            await asyncio.sleep(HELD)
            waited = not second.done()
        finally:
            # Ворота — в любом случае: иначе упавшая проверка оставила бы первую сессию с
            # замком, и чистка базы (`committed_sessions`) ждала бы её вечно.
            gate.set()
            pending = [task for task in (first, second) if task is not None]
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=15)

        assert waited, "«взять» не ждёт замка «в отказ»"
        first.result()
        assert second is not None
        with pytest.raises(LeadError, match=f"уже в отказе: отказал {REFUSER}"):
            second.result()
        async with factory() as session:
            reply = await session.get(ReplyModel, reply_id)
            assert reply is not None
            assert (reply.decision, reply.reviewed_by) == (ReplyDecision.REFUSED, REFUSER)
