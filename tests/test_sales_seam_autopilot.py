"""Ревью стыков, круг 2: агент продаж в бою — текст модели лиду уходит только руками человека.

Включено всё, что можно включить без правки кода: агент продаж в реестре этапов (тумблер
`SALES_AGENT_ENABLED`), выключатель автопилота сервера (`OUTREACH_AGENT_AUTOPILOT`) и режим
«автопилот» в версии настроек «Лидам» — записанный прямо в базу, мимо экрана, который его не
даёт. Задача черновика (`workers/agent_jobs.draft_answer`) пишет и судит черновик, а письма не
шлёт: автопилот этапу продаж закрыт флагом кода (`agent/stages.SALES_STAGE.autopilot`), и
транспорт почты задача даже не собирает. Черновик ждёт человека, группа продаж о нём узнаёт.

Мутанты, которые тест ловит: флаг кода у продаж включён; задача не спрашивает `refusal` этапа
перед автопилотом и сам автопилот его не спрашивает.

Модель ситуации и судьи — подставной HTTP, писатель — подставной; транспорт — записывающий.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.agent.settings import AUTOPILOT, AgentSettingsRepository
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.sales.agent import notify, parts
from backend.workers import agent_jobs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import (
    ALLOW,
    GOOD,
    INFORM,
    Writer,
    lead_replied,
    sales_on,
    stored,
)
from tests.test_sales_reply_routing import _Closable
from tests.test_thread_answer import Recording

__all__ = ["llm", "sales_on"]  # фикстуры — отсюда их видит pytest


class _ClosingWriter(Writer):
    """Писатель задачи: задача закрывает его сама."""

    async def aclose(self) -> None:
        return None


class _Recorded:
    """Почта задачи — записывающий транспорт на любой этап; собрана ли вообще — `built`."""

    def __init__(self) -> None:
        self.built = 0
        self.transport = Recording()

    def __call__(self) -> Recording:
        self.built += 1
        return self.transport


@pytest.mark.usefixtures("sales_on")
async def test_every_switch_on_and_the_sales_draft_still_waits_for_a_human(
    session: AsyncSession, llm: Plug, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply_id = await lead_replied(session)
    await AgentSettingsRepository(session).save(
        Stage.SALES, replace(parts.DEFAULTS, mode=AUTOPILOT), author="мимо экрана"
    )
    await session.commit()
    monkeypatch.setattr(outreach_cfg, "AGENT_AUTOPILOT", True)
    llm(situation=[INFORM], judge=[ALLOW])
    monkeypatch.setattr(agent_jobs, "AgentWriter", lambda: _ClosingWriter(GOOD))
    monkeypatch.setattr(agent_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        agent_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    transports = _Recorded()
    monkeypatch.setattr(agent_jobs, "Transports", transports)
    told: list[int] = []
    monkeypatch.setattr(notify, "queue_notice", told.append)

    report = await agent_jobs._draft_answer(reply_id)

    draft = await stored(session, reply_id)
    assert (report["status"], report["autopilot_sent"], report["autopilot_held"]) == (
        "drafted",
        None,
        None,
    )
    assert (draft.status, draft.body, draft.decided_by) == (DraftStatus.DRAFTED, GOOD, None)
    assert (transports.built, transports.transport.seen) == (0, [])
    answers = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.answers_reply_id == reply_id)
    )
    assert answers == 0, "ответ лиду ушёл без человека"
    assert told == [draft.id], "группа продаж о черновике не узнала"
