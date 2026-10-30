"""Письма, которые держит адрес лида, — ожидание, а не «отказ каждому письму окна» (находка 13.10).

Лид «готов», а его адрес не подтверждён проверкой (`unknown`) или проверен тестовым проверяльщиком
при живом: первое письмо держит правило адреса (`unconfirmed.first_letter`), и пересборка не
поможет — лида решит очистка. Автосборка такие письма не считает очередью (`Queued.held`). Проход
автоотправки брал их в окно, получал отказ каждому и через 15 минут поднимал тревогу «отказ каждому
письму окна» о письмах, которые ждут по делу. Теперь — тем же правилом — они не в окне: очередь из
одних таких — остановка по делу словами «ждут проверки адреса» (`Stop.HELD`), без тревоги; в
смешанном окне уходят только годные. Тревога автосборки (F7) видит такую очередь пустой: собрать
нечего — и сказать об этом надо.

База настоящая, почта — нулевой транспорт с памятью, тревога — подставная. Адреса выдуманы.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.ops.alarms import marked
from backend.features.sales import (
    auto_lines,
    autobuild,
    autobuild_alarm,
    autosend,
    autosend_jobs,
    autosend_window,
    queue,
)
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import Watch
from backend.features.sales.models import SalesLeadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_send import _seen, _transports

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    return found


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


async def _letters(
    session: AsyncSession, world: w.World, *emails: str, held: tuple[str, ...]
) -> dict[str, MessageModel]:
    """Первые письма лидов с проверенным адресом — сборкой; у лидов `held` после сборки адрес
    перепроверен: `unknown` (так оставляет их очистка, решение владельца 13.10)."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, verification_status="hunter:valid")
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    for email in held:
        lead = await session.scalar(select(SalesLeadModel).where(SalesLeadModel.email == email))
        assert lead is not None
        lead.verification_status = "hunter:unknown"
    await session.flush()
    rows = await session.scalars(select(MessageModel).order_by(MessageModel.id))
    return dict(zip(emails, rows.all(), strict=True))


def _on_test_base(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> list[Pass]:
    source = _transports()
    seen: list[Pass] = []

    async def work(now: datetime) -> Pass:
        found = await autosend.run(session, source, now=now)
        seen.append(found)
        return found

    monkeypatch.setattr(autosend_jobs, "_pass", work)
    return seen


async def test_a_window_of_held_letters_only_waits_in_words_without_an_alarm(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Оба письма окна держит адрес лида: проход их не берёт — ни отказа, ни «отказа каждому
    письму окна», ни тревоги и через 20 минут. Журнал говорит один раз, экран — каждый раз:
    письма ждут проверки адреса. Письма в очереди целы: их решит очистка."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    letters = await _letters(session, world, JANE, OLGA, held=(JANE, OLGA))
    passes = _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 5, 15, 20):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    line = await auto_lines.autosend_line(session, NOW)

    words = autosend_window.HELD.format(2)
    assert {(found.stop, found.stopped) for found in passes} == {(Stop.HELD, words)}
    assert [(found.sent, found.refused) for found in passes] == [({}, {})] * len(passes)
    assert told == []
    lines = [r.getMessage() for r in caplog.records if "автоотправка" in r.getMessage()]
    assert lines == [f"продажи: автоотправка стоит — {words}"]
    assert line.line.endswith(f"В окне получателя сейчас 0 писем. Стоит: {words}.")
    for letter in letters.values():
        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED


async def test_a_mixed_window_sends_only_the_letters_the_address_does_not_hold(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо Jane держит адрес, письмо Olga — нет: в окне только письмо Olga, оно уходит, а
    письмо Jane ждёт без отказа — тем же правилом, что у счёта очереди автосборки."""
    letters = await _letters(session, world, JANE, OLGA, held=(JANE,))
    source = _transports()

    seen = await autosend_window.in_window(session, NOW)
    counted = await autobuild.queued(session)
    found = await autosend.run(session, source, now=NOW)

    jane, olga = letters[JANE], letters[OLGA]
    assert (seen.ids, seen.queued, seen.held) == ((olga.id,), 1, 1)
    assert (counted.held, counted.ready) == (frozenset({jane.id}), frozenset({olga.id}))
    assert (found.sent, found.refused, found.stop) == ({olga.id: w.SALES_BOX}, {}, None)
    assert [outgoing.to for outgoing in _seen(source)] == [OLGA]
    await session.refresh(jane)
    assert jane.status is MessageStatus.QUEUED


async def test_a_queue_of_held_letters_the_cap_keeps_unbuilt_is_the_autobuild_alarm(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    told: list[str],
) -> None:
    """В очереди только письма, которые держит адрес, а потолок модели набран — автосборка не
    соберёт других: для тревоги автосборки (F7) такая очередь пуста — отправлять нечего."""
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    await _letters(session, world, JANE, held=(JANE,))
    await w.lead(session, world.hypothesis_id, OLGA, verification_status="hunter:valid")
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 41)
    usage.record(session, operation="sales_letter_rewrite", units=43)
    await session.flush()

    found = await autobuild.step(
        session, _AutoJobs(), now=datetime.now(UTC), watch=autobuild_alarm.Watch()
    )

    assert found.stuck
    assert told == [marked(f"{autobuild_alarm.STUCK}: {found.why}", autobuild_alarm.EMPTY)]
