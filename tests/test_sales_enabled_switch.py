"""Выключатель продаж (`SALES_ENABLED`): выключен — ни разбора ответа моделью, ни передачи лида.

Решение владельца по ревью стыков. Ответ в треде продаж приём принимает, как всегда: писем не
теряем, задача ставится. Задача разбора модель не зовёт — ни ради вида, ни ради отписки
словами (это тоже вид от модели), — ответ ждёт человека словами. Передача не начинается;
задача передачи и проход повторов в Kommo и Telegram не ходят: строки ждут, после включения
проход берёт их снова. Включён — всё как было.

Модель — подставная и считает вызовы; Kommo — `KommoFixture`, Telegram — подставной Bot API;
база — настоящая база дерева. Сети нет.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from backend.cli import sales_handoffs as console_cli
from backend.cli.main import main
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.core.domain import ReplyKind
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import outcome
from backend.features.replies.pipeline import Inbox
from backend.features.sales import handoff, handoff_jobs
from backend.features.sales.handoff import HandoffError
from backend.features.sales.kommo import KommoFixture
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel
from backend.features.sales.replies import SWITCHED_OFF, SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.workers import sales_jobs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_sales_handoff_jobs import wired
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_reply_handoff import TALK, Start
from tests.test_sales_reply_routing import (
    NOW,
    FakeClassifier,
    _Closable,
    _incoming,
    sales_letter,
    secret,
)
from tests.test_sales_telegram import Recorder

__all__ = ["secret", "wired"]  # подпись адреса ответа (2.1); задача и проход передачи на базе теста

STOP = "Please stop sending these emails."


def _switch(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", on)


def _job_on_test_base(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, model: FakeClassifier, start: Start
) -> None:
    """Тело задачи ответа — на базе теста; модель и передача — подставные, считают вызовы."""
    monkeypatch.setattr(sales_jobs, "KindClient", lambda: model)
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(sales_jobs, "SalesReplies", partial(SalesReplies, hand_over=start))


async def _accepted(session: AsyncSession, text: str) -> ReplyModel:
    """Ответ человека в треде продаж — настоящим приёмом: принят и отдан очереди продаж."""
    letter = await sales_letter(session)
    got = await Inbox(session, now=NOW).accept(_incoming(letter, text))
    assert got.to_sales == got.reply_id is not None, "приём писем не теряет"
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return reply


async def _reread(session: AsyncSession, reply: ReplyModel) -> ReplyModel:
    found = await session.get(ReplyModel, reply.id, populate_existing=True)
    assert found is not None
    return found


async def _handoffs(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesHandoffModel)) or 0)


# --- разбор ответа -------------------------------------------------------------------------


async def test_switched_off_the_job_calls_no_model_and_the_answer_waits_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Хочет говорить» при выключенных продажах: модели нет, передачи нет, ответ ждёт
    человека словами. Мутант «задача не смотрит на выключатель» зовёт модель и передаёт."""
    _switch(monkeypatch, on=False)
    reply = await _accepted(session, "Давайте созвонимся во вторник.")
    model, start = FakeClassifier(TALK), Start([])
    _job_on_test_base(monkeypatch, session, model, start)

    report = await sales_jobs.handle(reply.id)

    assert (model.calls, start.calls) == (0, []), "ни модели, ни передачи"
    assert (report["route"], report["waits"], report["reason"]) == ("manual", True, SWITCHED_OFF)
    # Причину читает человек на экране диалога — словами, без имени настройки.
    assert SWITCHED_OFF == "модуль продаж выключен — ответ ждёт человека"
    stored = await _reread(session, reply)
    assert (stored.model_parse or {}).get("kind") is None, "записка, а не вид"
    assert outcome.sales_review(stored.model_parse) == (True, SWITCHED_OFF)


async def test_switched_off_unsubscribe_in_words_is_not_done_either(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отписка словами — тоже вид от модели: при выключенных продажах адрес не закрывается,
    ответ ждёт человека."""
    _switch(monkeypatch, on=False)
    reply = await _accepted(session, STOP)
    assert reply.kind is ReplyKind.HUMAN
    model = FakeClassifier(KindFound(SalesKind.UNSUBSCRIBE, 0.97, quote=STOP))

    handled = await SalesReplies(session, model, threshold=0.8, now=NOW).handle(reply.id)

    await session.flush()
    assert (model.calls, handled.route, handled.waits) == (0, "manual", True)
    assert await session.scalar(select(func.count()).select_from(SuppressionModel)) == 0


async def test_switched_off_what_the_rules_recognised_goes_as_before(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отписку правилами узнал приём, без модели: модуль закрывает адрес и при выключенных
    продажах — выключатель останавливает модель и передачу, а не стоп-лист."""
    _switch(monkeypatch, on=False)
    reply = await _accepted(session, "remove me")
    assert reply.kind is ReplyKind.UNSUBSCRIBE
    model = FakeClassifier()

    handled = await SalesReplies(session, model, threshold=0.8, now=NOW).handle(reply.id)

    assert model.calls == 0
    assert "адрес закрыт во всех направлениях" in str(handled.reason)


async def test_switched_on_the_same_answer_is_sorted_by_the_model_and_handed_over(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _switch(monkeypatch, on=True)
    reply = await _accepted(session, "Давайте созвонимся во вторник.")
    model, start = FakeClassifier(TALK), Start([])
    _job_on_test_base(monkeypatch, session, model, start)

    report = await sales_jobs.handle(reply.id)

    assert (model.calls, [call[0] for call in start.calls]) == (1, [reply.thread_id])
    assert (report["kind"], report["route"]) == ("wants_to_talk", "handoff")


async def test_answer_left_while_switched_off_is_sorted_by_a_job_after_switching_on(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Записка выключенных продаж — не вид: задача, поставленная после включения, разбирает
    ответ заново, а не кончается «уже разобран»."""
    _switch(monkeypatch, on=False)
    reply = await _accepted(session, "Давайте созвонимся во вторник.")
    model, start = FakeClassifier(TALK), Start([])
    _job_on_test_base(monkeypatch, session, model, start)
    await sales_jobs.handle(reply.id)

    _switch(monkeypatch, on=True)
    report = await sales_jobs.handle(reply.id)

    assert (model.calls, report["kind"]) == (1, "wants_to_talk")
    assert len(start.calls) == 1


# --- передача лида -------------------------------------------------------------------------


async def test_switched_off_a_handoff_does_not_start(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = await sales_dialog(session)
    _switch(monkeypatch, on=False)
    queued: list[int] = []

    with pytest.raises(
        HandoffError, match=r"не начата: модуль продаж выключен — передача ждёт включения$"
    ):
        await handoff.start(session, dialog.thread.id, enqueue=queued.append)

    assert (queued, await _handoffs(session)) == ([], 0)


async def _waiting(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> SalesHandoffModel:
    """Передача, заведённая при включённых продажах, — её задача ещё не отработала."""
    _switch(monkeypatch, on=True)
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    row.due_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    return row


async def test_switched_off_the_handoff_job_and_the_pass_send_nothing_and_rows_wait(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выключили, пока передача ждала: задача не идёт ни в Kommo, ни в Telegram, проход ничего
    не ставит, строка не тронута. Мутанты «задача не смотрит на выключатель» и «проход берёт
    строки» шлют сообщение или ставят задачу."""
    row = await _waiting(session, monkeypatch)
    due_at = row.due_at
    kommo = KommoFixture()
    monkeypatch.setattr(handoff_jobs, "connected_kommo", lambda _http: kommo)
    queued: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", queued.append)
    _switch(monkeypatch, on=False)

    outcome_of_job = await handoff_jobs.run_hand_off(row.id)
    await handoff_jobs.retry_pass()

    assert outcome_of_job == {"handoff": row.id, "skipped": handoff.SWITCHED_OFF}
    assert (wired.seen, kommo.leads, queued) == ([], {}, [])
    kept = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert kept is not None
    assert (kept.kommo, kept.telegram) == (HandoffKommo.PENDING, HandoffTelegram.PENDING)
    assert (kept.claimed_at, kept.due_at) == (None, due_at), "строка ждёт как есть"


async def test_switched_on_again_the_pass_takes_the_waiting_handoff(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = await _waiting(session, monkeypatch)
    queued: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", queued.append)
    _switch(monkeypatch, on=False)
    await handoff_jobs.retry_pass()
    assert queued == []

    _switch(monkeypatch, on=True)
    await handoff_jobs.retry_pass()
    outcome_of_job = await handoff_jobs.run_hand_off(row.id)

    assert queued == [row.id]
    assert (outcome_of_job["kommo"], outcome_of_job["telegram"]) == ("off", "sent")
    [request] = wired.seen
    assert f"/threads/{row.thread_id}" in json.loads(request.content)["text"]


# --- повтор сообщения о лиде и консоль передач -------------------------------------------------


async def _message_waits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> SalesHandoffModel:
    """Сделка заведена, сообщение не ушло — ждёт повтора прохода (повтор только сообщения)."""
    row = await _waiting(session, monkeypatch)
    row.kommo, row.telegram = HandoffKommo.DONE, HandoffTelegram.UNDELIVERED
    row.telegram_tries, row.telegram_due_at = 1, row.due_at
    await session.commit()
    return row


def _queues(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """Очередь продаж — два списка: задачи передачи и повторы сообщения."""
    handoffs: list[int] = []
    messages: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", handoffs.append)
    monkeypatch.setattr(handoff_jobs, "enqueue_message", messages.append)
    return handoffs, messages


async def test_switched_off_the_pass_queues_no_message_retry_and_keeps_its_terms(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выключили, пока сообщение ждало повтора: проход его не ставит и сроков не двигает.
    Включили — проход ставит повтор, как раньше. Мутант «`message_due` не смотрит на
    выключатель» ставит задачу и сдвигает срок прохода."""
    row = await _message_waits(session, monkeypatch)
    due_at, message_at = row.due_at, row.telegram_due_at
    handoffs, messages = _queues(monkeypatch)
    _switch(monkeypatch, on=False)

    await handoff_jobs.retry_pass()

    assert (handoffs, messages, wired.seen) == ([], [], [])
    kept = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert kept is not None
    assert (kept.due_at, kept.telegram_due_at, kept.telegram_tries) == (due_at, message_at, 1)

    _switch(monkeypatch, on=True)
    await handoff_jobs.retry_pass()

    assert (handoffs, messages) == ([], [row.id])


async def test_switched_off_the_message_job_sends_nothing_and_leaves_the_row(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задача повтора сообщения (`resend_lead_message` → `run_hand_off(kommo=False)`) при
    выключенных продажах в Telegram не идёт и строку не трогает: ждёт включения, как
    передача. Включили — сообщение уходит. Мутант «выключатель — только у задачи передачи»
    шлёт сообщение и снимает срок повтора."""
    row = await _message_waits(session, monkeypatch)
    message_at = row.telegram_due_at
    _switch(monkeypatch, on=False)

    outcome_of_job = await handoff_jobs.run_hand_off(row.id, kommo=False)

    assert outcome_of_job == {"handoff": row.id, "skipped": handoff.SWITCHED_OFF}
    assert wired.seen == []
    kept = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert kept is not None
    assert (kept.telegram, kept.telegram_tries, kept.telegram_due_at, kept.claimed_at) == (
        HandoffTelegram.UNDELIVERED,
        1,
        message_at,
        None,
    )

    _switch(monkeypatch, on=True)
    outcome_of_job = await handoff_jobs.run_hand_off(row.id, kommo=False)

    assert outcome_of_job["telegram"] == "sent"
    assert len(wired.seen) == 1


async def _failed(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> SalesHandoffModel:
    """Kommo отказал — передача ждёт решения человека в консоли."""
    row = await _waiting(session, monkeypatch)
    row.kommo, row.telegram = HandoffKommo.FAILED, HandoffTelegram.SENT
    row.due_at, row.last_error = None, "Kommo: ключ Kommo отклонён (HTTP 401)"
    await session.commit()
    return row


async def _journal(session: AsyncSession, handoff_id: int) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.target == f"sales_handoff:{handoff_id}")
    )
    return list(rows)


async def test_switched_off_console_retry_is_refused_in_words_and_changes_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Повторить» при выключенных продажах: задача не пошла бы ни в Kommo, ни в Telegram —
    отказ словами и код 2, состояние и журнал не тронуты. Включили — как было. Мутант
    «консоль не смотрит на выключатель» переводит запись в `pending` и пишет журнал."""
    row = await _failed(session, monkeypatch)
    queued: list[int] = []
    _switch(monkeypatch, on=False)

    code = await console_cli.run_retry(session, row.id, enqueue=queued.append, now=NOW)

    assert code == console_cli.EXIT_REFUSED == 2
    assert (
        f"продажи выключены (SALES_ENABLED) — передача №{row.id} не поставлена; "
        "включите продажи и повторите"
    ) in capsys.readouterr().out
    kept = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert kept is not None
    assert (kept.kommo, kept.due_at, queued) == (HandoffKommo.FAILED, None, [])
    assert await _journal(session, row.id) == []

    _switch(monkeypatch, on=True)
    assert await console_cli.run_retry(session, row.id, enqueue=queued.append, now=NOW) == 0
    assert queued == [row.id]
    assert len(await _journal(session, row.id)) == 1


def test_switched_off_console_retry_through_main_is_refused_with_code_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)
    _switch(monkeypatch, on=False)

    assert main(["sales-handoff-retry", "987655"]) == 2

    assert (
        "продажи выключены (SALES_ENABLED) — передача №987655 не поставлена; "
        "включите продажи и повторите"
    ) in capsys.readouterr().out


async def test_switched_off_console_list_and_close_still_work(
    session: AsyncSession,
    wired: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Список только читает, «закрыть руками» наружу ничего не шлёт — при выключенных продажах
    оба работают. Мутанты «выключатель держит список» и «… держит закрытие» их роняют."""
    row = await _failed(session, monkeypatch)
    _switch(monkeypatch, on=False)

    assert await console_cli.run_list(session) == console_cli.EXIT_OK
    assert f"№{row.id} · лид №{row.lead_id}" in capsys.readouterr().out
    code = await console_cli.run_close(session, row.id, "сделка есть, нашёл по почте", 9301)

    assert code == console_cli.EXIT_OK
    kept = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert kept is not None
    assert (kept.kommo, kept.kommo_lead_id) == (HandoffKommo.DONE, 9301)
    assert len(await _journal(session, row.id)) == 1
    assert wired.seen == [], "закрытие руками в Telegram не ходит"
