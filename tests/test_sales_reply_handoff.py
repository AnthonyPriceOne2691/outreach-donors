"""Стык разбора ответа продаж (Ф2) и передачи лида (5.3): передача — после записи ответа.

«Хочет говорить» уходит в `handoff.start`: он коммитит сессию сам и ставит задачу,
которая идёт в Kommo и Telegram. Поэтому задача ответа зовёт его только после того,
как вид ответа записан (`sales_jobs.handle` → `SalesReplies.pass_on`), а отказ
передачи разбор не роняет. Всё — на настоящей базе дерева; модель и очередь —
подставные, в Kommo и Telegram тесты не ходят (задачу передачи никто не исполняет).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.outreach.threads import ThreadState, review_of, summarize
from backend.features.sales import handoff
from backend.features.sales.models import LeadStatus, SalesHandoffModel
from backend.features.sales.replies import HANDED_OVER, Handled, Route, SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.workers import sales_jobs
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_handoff_rows import Dialog, sales_dialog
from tests.test_sales_reply_routing import FakeClassifier, _Closable
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: путь вида ответа и передачи лида

#: Когда передача заведена — некруглое, заведомо выдуманное время.
AT = datetime(2026, 10, 14, 10, 23, tzinfo=UTC)

#: «Давайте созвонимся» — вид, который передаёт лида (текст ответа — `sales_dialog`).
TALK = KindFound(SalesKind.WANTS_TO_TALK, 0.93, quote="Давайте созвонимся во вторник")

#: Причина ответа «хочет говорить» после передачи и при её отказе — словами снимка.
NOT_HANDED = "хочет говорить: передать лида не вышло — "
HANDED = f"хочет говорить: {HANDED_OVER}"


class Start:
    """Передача лида — подставная: когда позвали и что к этому времени было записано."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        #: (диалог, транзакция задачи закрыта коммитом, вид в снимке ответа).
        self.calls: list[tuple[int, bool, object]] = []

    async def __call__(self, session: AsyncSession, thread_id: int) -> None:
        committed = not session.in_transaction()
        reply = await session.scalar(select(ReplyModel).where(ReplyModel.thread_id == thread_id))
        kind = (reply.model_parse or {}).get("kind") if reply is not None else None
        self.calls.append((thread_id, committed, kind))
        self.events.append("start")


def _job_on_test_base(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    found: KindFound,
    hand_over: Callable[[AsyncSession, int], Any],
    events: list[str],
) -> None:
    """Тело задачи — на базе теста, с подставной моделью и передачей; коммиты — в `events`."""
    factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def opened() -> AsyncIterator[AsyncSession]:
        async with factory() as job_session:
            commit = job_session.commit

            async def logged_commit() -> None:
                await commit()
                events.append("commit")

            monkeypatch.setattr(job_session, "commit", logged_commit)
            yield job_session

    monkeypatch.setattr(sales_jobs, "KindClient", lambda: FakeClassifier(found))
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(sales_jobs, "async_sessionmaker", lambda _engine, **_kw: opened)
    monkeypatch.setattr(sales_jobs, "SalesReplies", partial(SalesReplies, hand_over=hand_over))


def _queue_down(_handoff_id: int) -> None:
    # Не `RedisError`: отказ постановки другого рода — уже после записи строки передачи.
    raise RuntimeError("задача передачи не поставлена")


async def _reread(session: AsyncSession, dialog: Dialog) -> ReplyModel:
    reply = await session.get(ReplyModel, dialog.reply.id, populate_existing=True)
    assert reply is not None
    return reply


async def _state(session: AsyncSession, dialog: Dialog, reply: ReplyModel) -> ThreadState:
    letters = await session.scalars(
        select(MessageModel).where(MessageModel.thread_id == dialog.thread.id)
    )
    return summarize(list(letters), [reply], Stage.SALES).state


async def test_wants_to_talk_is_handed_over_after_the_answer_is_committed(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = await sales_dialog(session)
    events: list[str] = []
    start = Start(events)
    _job_on_test_base(monkeypatch, session, TALK, start, events)

    # Уровень журнала — боевой (`LOG_LEVEL=INFO`): строка задачи пишется на деле.
    with caplog.at_level(logging.INFO, logger="backend.features.sales.replies"):
        report = await sales_jobs.handle(dialog.reply.id)

    assert (report["kind"], report["route"]) == ("wants_to_talk", "handoff")
    assert events == ["commit", "start", "commit"], "передача — только после коммита снимка ответа"
    assert start.calls == [(dialog.thread.id, True, "wants_to_talk")]
    assert "лид передан телемаркетологу" in caplog.text
    assert (report["waits"], report["reason"]) == (False, HANDED)


async def test_handed_over_answer_no_longer_waits_for_a_human(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    dialog = await sales_dialog(session)
    queued: list[int] = []
    start = partial(handoff.start, enqueue=queued.append, now=lambda: AT)
    _job_on_test_base(monkeypatch, session, TALK, start, [])

    await sales_jobs.handle(dialog.reply.id)

    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None, "передача заведена"
    assert queued == [row.id], "и её задача поставлена"
    reply = await _reread(session, dialog)
    snap = reply.model_parse or {}
    assert (snap["kind"], snap["route"], snap["waits"]) == ("wants_to_talk", "handoff", False)
    review = review_of(reply, Stage.SALES)
    assert (review.waiting, review.reason) == (False, HANDED), "человеку писать не надо"
    assert reply.reviewed_at is None, "решения человека не было — его поля пусты"
    assert await _state(session, dialog, reply) is ThreadState.REPLIED


async def test_handoff_whose_job_was_not_queued_is_still_handed_over_by_the_retry_pass(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Строка передачи закоммичена, а задача не встала (отказ очереди любого рода, не только
    `RedisError`): передачу доведёт проход повторов — как при лежащем Redis. Ответ человека
    не ждёт: иначе человек написал бы лиду, которому через несколько минут позвонят.
    Ревью стыков (B5): раньше такой отказ оставлял ответ ждать со словами «пока — человек»."""
    dialog = await sales_dialog(session)
    start = partial(handoff.start, enqueue=_queue_down, now=lambda: AT)
    _job_on_test_base(monkeypatch, session, TALK, start, [])

    with caplog.at_level(logging.ERROR, logger="backend.features.sales.handoff"):
        report = await sales_jobs.handle(dialog.reply.id)

    assert (report["kind"], report["route"]) == ("wants_to_talk", "handoff"), "задача не упала"
    assert "задача передачи лида не поставлена" in caplog.text
    reply = await _reread(session, dialog)
    assert (reply.model_parse or {})["kind"] == "wants_to_talk", "разбор ответа записан"
    review = review_of(reply, Stage.SALES)
    assert (review.waiting, review.reason) == (False, HANDED), "ответ человека не ждёт"
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None, "строка передачи заведена до отказа"
    later = AT + timedelta(seconds=sales_cfg.HANDOFF_RETRY_SEC)
    assert await handoff.due(session, now=later) == [row.id], "её подберёт проход повторов"


async def test_handoff_refused_before_its_row_leaves_the_answer_sorted_and_waiting(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = await sales_dialog(session)
    dialog.lead.status = LeadStatus.REJECTED  # лида у диалога нет — передавать некого
    await session.flush()
    _job_on_test_base(monkeypatch, session, TALK, partial(handoff.start, now=lambda: AT), [])

    with caplog.at_level(logging.ERROR, logger="backend.features.sales.replies"):
        report = await sales_jobs.handle(dialog.reply.id)

    assert report["route"] == "handoff", "задача не упала"
    assert "нет лида продаж" in caplog.text, "почему передачи нет — в журнале"
    reply = await _reread(session, dialog)
    snap = reply.model_parse or {}
    assert snap["kind"] == "wants_to_talk"
    # Ревью стыков (B5): почему передачи нет — и в снимке ответа, а не только в журнале:
    # человеку на экране видно, что чинить, а не просто «пока — человек».
    why = (
        f"у диалога №{dialog.thread.id} нет лида продаж: ни ссылкой на адрес, "
        "ни почтой на его домене"
    )
    assert snap["handoff_error"] == why
    review = review_of(reply, Stage.SALES)
    assert (review.waiting, review.reason) == (True, f"{NOT_HANDED}{why}; решает человек")
    assert report["reason"] == review.reason
    assert await session.scalar(select(SalesHandoffModel.id)) is None


async def test_reason_that_cannot_reach_the_snapshot_stays_in_the_log(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Передача упала вместе с базой: сессия задачи не годится и для записи причины —
    ответ остаётся со снимком разбора, причина — в журнале, задача не падает."""

    class Broken:
        async def get(self, *_args: object, **_kwargs: object) -> None:
            raise OperationalError("SELECT replies", {}, Exception("база недоступна"))

    async def refuse(_session: AsyncSession, _thread_id: int) -> None:
        raise OperationalError("INSERT sales_handoffs", {}, Exception("база недоступна"))

    sales = SalesReplies(Broken(), FakeClassifier(TALK), hand_over=refuse)  # type: ignore[arg-type]
    handled = Handled(5, kind="wants_to_talk", route="handoff", waits=True, handoff_thread=7)

    with caplog.at_level(logging.ERROR, logger="backend.features.sales.replies"):
        after = await sales.pass_on(handled)

    assert after == handled
    assert "в снимок ответа не записать" in caplog.text


@pytest.mark.parametrize(
    ("found", "waits"),
    [
        (KindFound(SalesKind.QUESTION, 0.91, quote="Давайте созвонимся"), True),
        (KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="Давайте созвонимся"), False),
        (KindFound(SalesKind.WANTS_TO_TALK, 0.55, quote="Давайте созвонимся"), True),
    ],
    ids=["question", "not_interested", "wants_to_talk-below-threshold"],
)
async def test_job_does_not_hand_over_any_other_answer(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, found: KindFound, waits: bool
) -> None:
    dialog = await sales_dialog(session)
    events: list[str] = []
    start = Start(events)
    _job_on_test_base(monkeypatch, session, found, start, events)

    await sales_jobs.handle(dialog.reply.id)

    assert (events, start.calls) == (["commit"], [])
    review = review_of(await _reread(session, dialog), Stage.SALES)
    assert review.waiting is waits, "ожидание решает путь вида, а не передача"
    assert HANDED_OVER not in str(review.reason)


@pytest.mark.parametrize("kind", list(SalesKind), ids=[kind.value for kind in SalesKind])
async def test_only_wants_to_talk_above_the_threshold_is_marked_for_handoff(
    session: AsyncSession, kind: SalesKind
) -> None:
    dialog = await sales_dialog(session)
    start = Start([])
    sales = SalesReplies(session, FakeClassifier(KindFound(kind, 0.95)), hand_over=start)

    handled = await sales.handle(dialog.reply.id)

    expected = dialog.thread.id if kind is SalesKind.WANTS_TO_TALK else None
    assert handled.handoff_thread == expected
    assert (handled.route == Route.HANDOFF.value) is (expected is not None)
    assert start.calls == [], "внутри разбора передача не зовётся"
