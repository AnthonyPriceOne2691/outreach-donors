"""Проход переадресации в диалогах рекламодателей — проводка (`letters/redirect_send.py`).

Идёт в процессе разбора (`workers/reaper.py`) тем же циклом, что очередь писем, которая уходит
сама: письмо на адрес из ответа — тоже первое письмо без человека, в рабочие часы получателя и
в лимитах ящика. Своя у прохода только проводка: база, клиент модели, почта и рубильник
(`OUTREACH_LEAD_REDIRECT`). Делать нечего — ни почты, ни клиента модели проход не собирает.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters import redirect_send
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.replies import lead_redirect
from backend.features.replies.lead_redirect import State
from backend.features.replies.redirect_ask import RedirectClient

logger = logging.getLogger(__name__)


async def _anything(session: AsyncSession) -> bool:
    """Есть ли проходу работа: решение ждёт или письмо заведено. Дешёвый вопрос до почты."""
    found = await session.scalar(
        select(exists().where(LeadRedirectModel.state.in_((State.PENDING, State.LETTER))))
    )
    return bool(found)


async def redirect_pass() -> None:
    """Один проход: решения по ответам «пишите на …» и письма, которые ждут отправки."""
    if not lead_redirect.enabled():
        return
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            if not await _anything(session):
                return
        client = RedirectClient()
        try:
            async with factory() as session, in_use(Transports()) as transports:
                passed = await redirect_send.run(session, client, transports, now=datetime.now(UTC))
        finally:
            await client.aclose()
        if passed.decided:
            logger.info("переадресация: решено — %s", passed.decided)
        if passed.delivered:
            logger.debug("переадресация: письма — %s", passed.delivered)
    finally:
        await engine.dispose()
