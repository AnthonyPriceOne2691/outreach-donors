"""Договор моста почты (#211, #216) со стороны настоящего модуля продаж — срез 4.6b.

Мост спрашивает модуль в своей точке сохранения и пропускает от него только отказ словами
почты (`MailRefusalError`); всё другое — поломка модуля: «не подключены» и трасса в журнале.
Поэтому модуль (`sales/mail.py`) ничего не сохраняет внутри ответа, а добивку, которую не
собрать, называет «пока нельзя» (`SalesNotConnectedError`), — срок возвращается без записи
о поломке. Подставной модуль и сама точка сохранения — `test_sales_stage_bridge.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core import stages
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import FIRST_DUE, _first_sent, _lead_of, _queued

Ask = Callable[[AsyncSession, MessageModel], Awaitable[object]]

#: Каждый вопрос почты модулю продаж — через мост, как его задаёт почта.
ASKS: dict[str, Ask] = {
    "recipient": lambda session, letter: stages.recipient(
        session, letter, Stage.SALES, None, f"Письмо №{letter.id}"
    ),
    "check": stages.check_sales,
    "connected": lambda session, _letter: stages.sales_connected(session),
    "followup": lambda session, letter: stages.sales_followup(session, letter.thread_id, 1),
    "policy": lambda session, letter: stages.mail_policy(session, Stage.SALES, "Письмо"),
}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.mark.parametrize("ask", sorted(ASKS))
async def test_module_answers_neither_commit_nor_roll_back(
    session: AsyncSession, world: w.World, ask: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «модуль сохраняет внутри ответа»: несохранённое почты ушло бы в базу с ним,
    а откат внутри ответа снял бы точку сохранения моста."""
    [letter] = await _queued(session, world)
    seen: list[str] = []
    for name in ("commit", "rollback"):
        monkeypatch.setattr(session, name, _spy(seen, name, getattr(session, name)))

    await ASKS[ask](session, letter)

    assert seen == []


def _spy(seen: list[str], name: str, real: Callable[[], Awaitable[None]]) -> object:
    async def spy() -> None:
        seen.append(name)
        await real()

    return spy


async def test_followup_that_cannot_be_built_waits_and_is_not_a_module_failure(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    """Имя лида стёрли после первого письма: добивку не собрать — срок вернулся, а журнал
    говорит почему, без «ошибки модуля продаж» с трассой (её мост пишет о поломке)."""
    source, [first] = await _first_sent(session, world)
    lead = await _lead_of(session, first)
    lead.name = None
    await session.flush()

    with caplog.at_level(logging.INFO):
        report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (report.sent, report.postponed) == (0, 1)
    await session.refresh(first)
    assert first.next_action_at == FIRST_DUE + followups.POSTPONE
    assert f"Добивка шага 1 в переписке №{first.thread_id}: не собрана — " in caplog.text
    assert "подстановка без значения" in caplog.text
    assert "ошибка модуля продаж" not in caplog.text
