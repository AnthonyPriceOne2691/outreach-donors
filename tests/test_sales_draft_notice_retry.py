"""Повтор сообщения о черновике агента продаж по расписанию — решение владельца.

Та же серия, что у сообщения о лиде (`telegram_series.py`): не ушло из-за сети, 5xx или 429 —
повтор проходом (`handoff_jobs.retry_pass` → `notify_retry.due`), пауза 5, 10, 20, 40 мин, у 429 —
не меньше паузы Telegram; попыток пять, после последней — тревога словами; постоянный отказ —
без повтора и с тревогой сразу. Повторяется только сообщение о нынешней версии черновика,
который ждёт человека; продажи (`SALES_ENABLED`) или агент продаж (`SALES_AGENT_ENABLED`)
выключены — повторов нет, строки ждут.

Оснастка — `test_sales_draft_notify.py`: черновик настоящим путём шва, бот продаж поверх
подставного Bot API, паузы бота не спят, база — настоящая база дерева. Время — выдуманное.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import rq
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import sales as cfg
from backend.features.agent import drafting
from backend.features.core.domain import DraftStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales import handoff, handoff_jobs
from backend.features.sales.agent import notify, notify_retry
from backend.features.sales.models import (
    HandoffKommo,
    HandoffTelegram,
    NoticeStatus,
    SalesDraftNoticeModel,
)
from backend.features.sales.telegram import SalesBot
from backend.shared import queue as shared_queue
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import GOOD, INFORM, Writer, lead_replied, sales_on
from tests.test_sales_draft_notify import (
    ALLOW,
    APP,
    IMPORTED_QUEUE,
    TOKEN,
    BotApi,
    Queue,
    _Closable,
    alerts,
    down,
    drafted,
    journal,
    ok,
    pauses,
    queue,
    wired,
)
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_model import ROOT
from tests.test_sales_telegram import refused

__all__ = ["alerts", "llm", "pauses", "queue", "sales_on", "wired"]  # фикстуры — их видит pytest

pytestmark = pytest.mark.usefixtures("sales_on", "switched_on", "queue")

NOW = datetime(2026, 10, 15, 9, 41, 7, tzinfo=UTC)
#: Строка журнала, когда продажи выключены: сообщения нет, попытка не потрачена.
HELD = "не отправлено: продажи выключены (SALES_ENABLED)"

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
MINUTE = timedelta(minutes=1)
REVISION = "d2afdd4a00d0_sales_draft_notices_retry.py"


class Alarms(list[str]):
    """Тревоги эксплуатации — в список: бот тревог в тестах не ходит в сеть."""

    async def __call__(self, text: str) -> bool:
        self.append(text)
        return True


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи и агент продаж включены — проход и задача повтора работают."""
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(cfg, "AGENT_ENABLED", True)


def flood(seconds: int) -> httpx.Response:
    """429 с паузой, которую назвал Telegram."""
    return httpx.Response(
        429,
        json={
            "ok": False,
            "description": "Too Many Requests",
            "parameters": {"retry_after": seconds},
        },
    )


async def first(
    session: AsyncSession, llm: Plug, api: BotApi, alarms: Alarms, at: datetime = NOW
) -> SalesDraftNoticeModel:
    """Черновик и задача его сообщения в минуту `at` — та, что ставит крючок шва."""
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    await notify.notify(
        session, outcome.draft_id, SalesBot(api.client()), alert=alarms, now=lambda: at
    )
    [row] = await journal(session)
    return row


async def again(
    session: AsyncSession, row: SalesDraftNoticeModel, api: BotApi, alarms: Alarms, at: datetime
) -> dict[str, Any]:
    """Повтор прохода в минуту `at`: проход берёт строку, задача повторяет сообщение."""
    taken = await notify_retry.due(session, now=at)
    assert taken == [(row.id, row.tries)], "проход не взял строку, которой пора"
    done = await notify_retry.resend(
        session, row.id, row.tries, SalesBot(api.client()), alert=alarms, now=lambda: at
    )
    await session.refresh(row)
    return done


# --- серия: пауза растёт, последняя попытка — тревога ---------------------------------------


async def test_first_task_that_did_not_go_waits_for_the_pass_without_an_alarm(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    api, alarms = BotApi(down()), Alarms()

    row = await first(session, llm, api, alarms)

    assert (row.status, row.tries, row.due_at) == (
        NoticeStatus.UNDELIVERED,
        1,
        NOW + timedelta(minutes=5),
    )
    assert row.error is not None
    assert "повтор не раньше 15.10 09:46 UTC (попытка 1 из 5)" in row.error
    assert alarms == [], "до последней попытки тревоги нет"
    assert await notify_retry.due(session, now=NOW + timedelta(minutes=4)) == [], "раньше срока"


async def test_the_pass_retries_with_a_growing_pause_and_the_last_try_is_an_alarm(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    assert row.due_at is not None
    pauses = [row.due_at - NOW]

    while row.due_at is not None:
        at = row.due_at
        assert alarms == [], "до последней попытки тревоги нет"
        await again(session, row, api, alarms, at)
        if row.due_at is not None:
            pauses.append(row.due_at - at)

    assert pauses == [timedelta(minutes=m) for m in (5, 10, 20, 40)]
    assert len(api.seen) == 5 * 3, "пять попыток, в каждой бот пробует трижды"
    assert (row.status, row.tries, row.due_at) == (NoticeStatus.UNDELIVERED, 0, None)
    [alarm] = alarms
    assert alarm.startswith(
        f"продажи: сообщение о черновике №{row.draft_id} не доставлено в группу продаж "
        "за 5 попыток — повторов больше нет"
    )
    assert f"Черновик цел: {APP}/threads/" in alarm
    assert TOKEN not in alarm
    assert row.error is not None
    assert row.error.endswith("попыток — 5, повторов больше нет")
    assert await notify_retry.due(session, now=NOW + timedelta(days=3)) == []


async def test_retry_that_goes_through_is_sent_and_the_series_ends(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    api.answers = [ok()]

    done = await again(session, row, api, alarms, NOW + timedelta(minutes=5))

    assert done["status"] == "sent"
    assert (row.status, row.error, row.tries, row.due_at) == (NoticeStatus.SENT, None, 0, None)
    assert alarms == []
    assert await notify_retry.due(session, now=NOW + timedelta(days=3)) == []


@pytest.mark.parametrize(
    ("asked", "pause"),
    [(4000, timedelta(seconds=4000)), (7, timedelta(minutes=5))],
    ids=["telegram-longer", "ours-longer"],
)
async def test_429_waits_not_less_than_telegram_asked(
    session: AsyncSession, llm: Plug, wired: list[float], asked: int, pause: timedelta
) -> None:
    api, alarms = BotApi(flood(asked)), Alarms()

    row = await first(session, llm, api, alarms)

    assert (row.tries, row.due_at) == (1, NOW + pause)
    assert alarms == []


# --- постоянный отказ и бот без токена — без повтора ------------------------------------------


@pytest.mark.parametrize("cause", ["chat not found", "bot blocked", "control character"])
async def test_permanent_refusal_is_not_retried_and_alarms_at_once(
    session: AsyncSession,
    llm: Plug,
    wired: list[float],
    monkeypatch: pytest.MonkeyPatch,
    cause: str,
) -> None:
    api, alarms = BotApi(ok()), Alarms()
    if cause == "chat not found":
        api.answers = [refused(400, "Bad Request: chat not found")]
    elif cause == "bot blocked":
        api.answers = [refused(403, "Forbidden: bot was kicked from the group chat")]
    else:
        monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", f"{TOKEN[:10]}\t{TOKEN[10:]}")

    row = await first(session, llm, api, alarms)

    assert (row.status, row.tries, row.due_at) == (NoticeStatus.UNDELIVERED, 0, None)
    assert len(api.seen) <= 1, "постоянный отказ бот не повторяет"
    [alarm] = alarms
    assert "не доставлено в группу продаж — " in alarm
    assert await notify_retry.due(session, now=NOW + timedelta(days=3)) == []


async def test_bot_without_a_token_is_not_retried(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без токена Telegram не зовётся и серии нет: о таких черновиках — сводная тревога сторожа
    бота, а не повтор."""
    api, alarms = BotApi(ok()), Alarms()
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "")

    row = await first(session, llm, api, alarms)

    assert (api.seen, row.tries, row.due_at) == ([], 0, None)
    assert "SALES_TELEGRAM_BOT_TOKEN" in (row.error or "")
    assert await notify_retry.due(session, now=NOW + timedelta(days=3)) == []


# --- только о нынешней версии черновика, который ждёт человека -------------------------------


@pytest.mark.parametrize("decided", [DraftStatus.SENT, DraftStatus.REJECTED])
async def test_decided_draft_is_not_retried(
    session: AsyncSession, llm: Plug, wired: list[float], decided: DraftStatus
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    tries = row.tries
    draft = await session.get(AgentDraftModel, row.draft_id)
    assert draft is not None
    draft.status = decided
    await session.commit()
    seen = len(api.seen)

    taken = await notify_retry.due(session, now=NOW + timedelta(hours=1))
    done = await notify_retry.resend(session, row.id, tries, SalesBot(api.client()), alert=alarms)

    assert taken == []
    assert done["skipped"] == f"черновик в состоянии «{decided.value}» — человека он не ждёт"
    assert (len(api.seen), alarms) == (seen, [])


async def test_rewritten_draft_does_not_retry_the_old_version(
    session: AsyncSession, llm: Plug, wired: list[float], queue: Queue
) -> None:
    """«Написать заново» — новая версия и своё сообщение (крючок шва); повтор прежней не идёт:
    проход её не берёт, а задача, поставленная раньше, сверяет версию — даже когда сообщение
    о новой версии само ждёт повтора с тем же счётом."""
    api, alarms = BotApi(down()), Alarms()
    old = await first(session, llm, api, alarms)
    draft = await session.get(AgentDraftModel, old.draft_id)
    assert draft is not None
    rewritten = await drafting.draft_answer(session, Writer(GOOD), draft.reply_id, again=True)
    await session.commit()
    await drafting.announce(rewritten)
    assert [job for _, (job,) in queue.jobs][-1] == old.draft_id, "о новой версии — своя задача"
    later = NOW + timedelta(hours=1)
    bot = SalesBot(api.client())
    await notify.notify(session, old.draft_id, bot, alert=alarms, now=lambda: later)
    (new,) = [row for row in await journal(session) if row.id != old.id]
    assert (new.tries, old.tries) == (1, 1)
    seen = len(api.seen)

    taken = await notify_retry.due(session, now=later)
    done = await notify_retry.resend(session, old.id, old.tries, bot, alert=alarms)

    assert taken == []
    assert done["skipped"] == (
        "черновик переписан — о новой версии своё сообщение, повтор прежней не идёт"
    )
    assert len(api.seen) == seen
    await session.refresh(new)
    assert (new.tries, new.due_at) == (1, later + timedelta(minutes=5)), "срок новой не тронут"


async def test_retry_already_made_by_another_task_is_not_repeated(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    await again(session, row, api, alarms, NOW + timedelta(minutes=5))
    assert row.tries == 2
    seen = len(api.seen)

    late = await notify_retry.resend(session, row.id, 1, SalesBot(api.client()), alert=alarms)

    assert late["skipped"] == "этот повтор уже сделан — повторять нечего"
    assert len(api.seen) == seen


async def test_pass_does_not_take_what_it_took_until_the_task_ran(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    at = NOW + timedelta(minutes=5)

    assert await notify_retry.due(session, now=at) == [(row.id, 1)]
    assert await notify_retry.due(session, now=at + MINUTE) == [], "взятое — не вторым кругом"
    lost = at + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    assert await notify_retry.due(session, now=lost) == [(row.id, 1)], "потерянную задачу — снова"


async def test_first_task_does_not_jump_the_pass_and_after_the_alarm_tries_silently(
    session: AsyncSession, llm: Plug, wired: list[float]
) -> None:
    """Задача не прохода о версии, которая ждёт повтора, срок не перебивает (429 — просьба
    Telegram). После итога — попытки кончились — пробует ещё раз молча: без второй тревоги и без
    новой серии."""
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    bot = SalesBot(api.client())

    early = await notify.notify(session, row.draft_id, bot, alert=alarms, now=lambda: NOW)
    while row.due_at is not None:
        await again(session, row, api, alarms, row.due_at)
    assert len(alarms) == 1
    late = await notify.notify(session, row.draft_id, bot, alert=alarms, now=lambda: NOW)

    assert early.skipped == "сообщение об этой версии ждёт повтора прохода по расписанию"
    assert late.status is NoticeStatus.UNDELIVERED
    await session.refresh(row)
    assert (row.tries, row.due_at, len(alarms)) == (0, None, 1)


# --- выключатели: продажи и агент продаж ---------------------------------------------------------


@pytest.mark.parametrize("switch", ["ENABLED", "AGENT_ENABLED"])
async def test_switched_off_no_retries_and_rows_wait(
    session: AsyncSession,
    llm: Plug,
    wired: list[float],
    monkeypatch: pytest.MonkeyPatch,
    switch: str,
) -> None:
    """Продажи или агент продаж выключены: проход ничего не берёт и сроков не трогает, задача в
    Telegram не ходит и строку не меняет. Включили — проход берёт строку."""
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms)
    due_at, tries, seen = row.due_at, row.tries, len(api.seen)
    monkeypatch.setattr(cfg, switch, False)
    later = NOW + timedelta(hours=1)

    taken = await notify_retry.due(session, now=later)
    done = await notify_retry.resend(session, row.id, tries, SalesBot(api.client()), alert=alarms)

    assert (taken, done["skipped"]) == ([], notify_retry.SWITCHED_OFF)
    assert len(api.seen) == seen
    await session.refresh(row)
    assert (row.due_at, row.tries, row.status) == (due_at, tries, NoticeStatus.UNDELIVERED)

    monkeypatch.setattr(cfg, switch, True)
    assert await notify_retry.due(session, now=later) == [(row.id, tries)]


# --- проход: тем же кругом, после повторов передачи; сторож бота — после них ----------------------


async def _three_due(
    session: AsyncSession, llm: Plug, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Проходу пора: передаче (Kommo не ответил), сообщению о лиде и сообщению о черновике.
    Очередь — список; проход работает в транзакции теста. Возвращает список постановок и
    ожидаемый порядок."""
    api, alarms = BotApi(down()), Alarms()
    notice = await first(session, llm, api, alarms, at=datetime.now(UTC) - timedelta(hours=1))
    rows = {}
    for name in ("kommo", "message"):
        dialog = await sales_dialog(
            session, host=f"{name}.example.test", email=f"a@{name}.example.test"
        )
        rows[name] = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    past = datetime.now(UTC) - MINUTE
    kommo, message = rows["kommo"], rows["message"]
    kommo.kommo, kommo.telegram = HandoffKommo.RETRY, HandoffTelegram.SENT
    message.kommo, message.telegram = HandoffKommo.DONE, HandoffTelegram.UNDELIVERED
    message.telegram_tries, message.telegram_due_at = 1, past
    kommo.due_at = message.due_at = past
    await session.commit()
    order: list[tuple[str, int]] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", lambda n: order.append(("handoff", n)))
    monkeypatch.setattr(handoff_jobs, "enqueue_message", lambda n: order.append(("message", n)))
    monkeypatch.setattr(handoff_jobs, "queue_resend", lambda n, _t: order.append(("notice", n)))
    monkeypatch.setattr(handoff_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        handoff_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return order, [("handoff", kommo.id), ("message", message.id), ("notice", notice.id)]


async def test_retry_pass_queues_notice_retries_after_handoff_retries(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    order, expected = await _three_due(session, llm, monkeypatch)

    await handoff_jobs.retry_pass()

    assert order == expected


async def test_bot_watch_goes_after_the_retries_and_does_not_hold_them(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сторож бота продаж (сводная тревога «без токена») идёт тем же кругом после постановки
    повторов: его сбой не держит ни повтор передачи, ни повторы сообщений."""
    order, expected = await _three_due(session, llm, monkeypatch)

    async def broken(_session: AsyncSession) -> None:
        raise RuntimeError("лента тревог не ответила")

    monkeypatch.setattr(handoff_jobs, "watch_token", broken)

    with pytest.raises(RuntimeError, match="лента тревог не ответила"):
        await handoff_jobs.retry_pass()

    assert order == expected


# --- продажи выключены: сообщения о черновике нет ------------------------------------------------


class _ButtonWriter(Writer):
    """Писатель кнопки «написать заново» на месте модели; маршрут закрывает его сам."""

    def __init__(self) -> None:
        super().__init__(GOOD)

    async def aclose(self) -> None:
        return None


async def _no_message(session: AsyncSession, draft_id: int) -> tuple[SalesDraftNoticeModel, BotApi]:
    """Задача сообщения о черновике при выключенных продажах — в минуту `NOW`."""
    api, alarms = BotApi(ok()), Alarms()
    held = await notify.notify(
        session, draft_id, SalesBot(api.client()), alert=alarms, now=lambda: NOW
    )
    assert (api.seen, alarms) == ([], []), "Telegram при выключенных продажах не зовётся"
    assert (held.status, held.error) == (NoticeStatus.UNDELIVERED, HELD)
    [row] = await journal(session)
    assert (row.status, row.error, row.tries, row.due_at) == (
        NoticeStatus.UNDELIVERED,
        HELD,
        0,
        NOW,
    )
    return row, api


async def test_switched_off_the_button_writes_the_draft_and_its_message_waits(
    session: AsyncSession,
    llm: Plug,
    wired: list[float],
    queue: Queue,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Написать заново» при выключенных продажах: черновик пишется, как раньше (шов агента
    выключатель продаж не читает), а сообщения нет — Telegram ни разу, строка «не отправлено»
    со сроком, попытка не потрачена. Мутант «выключатель не держит сообщение» шлёт его."""
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])
    reply = await session.get(ReplyModel, reply_id)
    assert reply is not None
    path = f"/api/threads/{reply.thread_id}/replies/{reply_id}/draft"
    await session.commit()
    monkeypatch.setattr("backend.api.threads.routes.AgentWriter", _ButtonWriter)
    monkeypatch.setattr(cfg, "ENABLED", False)
    await make_user("admin@notice-off.example.test", role=UserRole.ADMIN)
    headers = bearer(await sign_in("admin@notice-off.example.test"))

    written = await client.post(path, headers=headers)

    assert written.status_code == 200, written.text
    assert written.json()["body"] == GOOD
    [(job, (draft_id,))] = queue.jobs
    assert job == notify.NOTICE_JOB
    await _no_message(session, draft_id)


@pytest.mark.parametrize("queued", ["after the switch-off", "before the switch-off"])
async def test_switched_off_the_draft_job_sends_no_message_and_it_waits(
    session: AsyncSession,
    llm: Plug,
    wired: list[float],
    monkeypatch: pytest.MonkeyPatch,
    queued: str,
) -> None:
    """Задача черновика, поставленная до выключения, пишет его после — а сообщения нет; и
    задача сообщения, поставленная до выключения, тоже не шлёт."""
    if queued == "after the switch-off":
        monkeypatch.setattr(cfg, "ENABLED", False)
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    monkeypatch.setattr(cfg, "ENABLED", False)

    await _no_message(session, outcome.draft_id)


async def test_switched_on_again_the_pass_sends_the_waiting_message(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Включили — проход берёт строку «не отправлено» сам: черновик ждёт человека, версия та же.
    Мутант «без срока повтора» оставляет её навсегда."""
    monkeypatch.setattr(cfg, "ENABLED", False)
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    row, api = await _no_message(session, outcome.draft_id)
    assert await notify_retry.due(session, now=NOW) == [], "выключены — проход не берёт"

    monkeypatch.setattr(cfg, "ENABLED", True)
    done = await again(session, row, api, Alarms(), NOW)

    assert done["status"] == "sent"
    assert len(api.seen) == 1
    assert (row.status, row.error, row.tries, row.due_at) == (NoticeStatus.SENT, None, 0, None)


async def test_draft_decided_before_switching_on_is_not_announced(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ENABLED", False)
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    row, api = await _no_message(session, outcome.draft_id)
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    draft.status = DraftStatus.REJECTED
    await session.commit()

    monkeypatch.setattr(cfg, "ENABLED", True)
    taken = await notify_retry.due(session, now=NOW + timedelta(hours=1))
    done = await notify_retry.resend(session, row.id, 0, SalesBot(api.client()), alert=Alarms())

    assert taken == []
    assert done["skipped"] == "черновик в состоянии «rejected» — человека он не ждёт"
    assert api.seen == []


async def test_switched_off_a_message_whose_end_was_told_stays_as_it_is(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Итог сказан (отказ постоянный, тревога ушла): задача при выключенных продажах строку не
    переписывает и новой серии не заводит — после включения проход её не берёт."""
    api, alarms = BotApi(refused(400, "Bad Request: chat not found")), Alarms()
    told = await first(session, llm, api, alarms)
    error = told.error
    monkeypatch.setattr(cfg, "ENABLED", False)

    held = await notify.notify(
        session, told.draft_id, SalesBot(api.client()), alert=alarms, now=lambda: NOW
    )

    assert held.skipped == HELD
    await session.refresh(told)
    assert (told.error, told.tries, told.due_at) == (error, 0, None)
    monkeypatch.setattr(cfg, "ENABLED", True)
    assert await notify_retry.due(session, now=NOW + timedelta(days=1)) == []


# --- задача очереди и ревизия ------------------------------------------------------------------


def test_resend_goes_to_the_sales_queue_with_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    placed: list[tuple[str, str, tuple[Any, ...], list[str]]] = []

    def enqueue(self: rq.Queue, path: str, *args: Any, **options: Any) -> None:
        placed.append((self.name, path, args, sorted(options)))

    monkeypatch.setattr(rq.Queue, "enqueue", enqueue)
    monkeypatch.setattr(notify, "sales_queue", IMPORTED_QUEUE)

    notify.queue_resend(52, 3)

    assert placed == [
        (shared_queue.SALES_QUEUE_NAME, notify.RESEND_JOB, (52, 3), ["result_ttl", "retry"])
    ]


def test_resend_job_imports_first_in_a_clean_process() -> None:
    module, _, name = notify.RESEND_JOB.rpartition(".")
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}; assert callable({module}.{name})"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert getattr(notify_retry, name) is notify_retry.resend_draft_notice


def test_resend_job_runs_the_core_in_its_own_loop_and_remembers_why_it_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify_retry, "setup_logging", lambda: None)
    monkeypatch.setattr(notify_retry, "check_storage", lambda: None)
    remembered: list[tuple[str, str]] = []

    class Job:
        id = "sales-notice-retry-test"

    async def done(notice_id: int, tries: int) -> dict[str, Any]:
        return {"notice": notice_id, "tries": tries}

    monkeypatch.setattr(notify_retry, "run_resend", done)
    assert notify_retry.resend_draft_notice(52, 3) == {"notice": 52, "tries": 3}

    async def broken(_notice_id: int, _tries: int) -> dict[str, Any]:
        raise RuntimeError("база отвалилась")

    monkeypatch.setattr(notify_retry, "run_resend", broken)
    monkeypatch.setattr(notify_retry, "get_current_job", Job)
    monkeypatch.setattr(
        notify_retry, "remember_job_error", lambda job_id, why: remembered.append((job_id, why))
    )
    with pytest.raises(RuntimeError, match="база отвалилась"):
        notify_retry.resend_draft_notice(52, 3)
    assert remembered == [("sales-notice-retry-test", "RuntimeError: база отвалилась")]


async def test_resend_job_runs_on_its_own_session_and_client(
    session: AsyncSession, llm: Plug, wired: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, alarms = BotApi(down()), Alarms()
    row = await first(session, llm, api, alarms, at=datetime.now(UTC) - timedelta(hours=1))
    api.answers = [ok()]
    monkeypatch.setattr(notify_retry, "_http", api.client)
    monkeypatch.setattr(notify_retry, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        notify_retry,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    report = await notify_retry.run_resend(row.id, row.tries)

    assert (report["notice"], report["status"]) == (row.id, "sent")


def _columns_down_and_up(connection: Connection) -> tuple[bool, bool]:
    """Ревизия повтора вниз и вверх на соединении теста."""
    migration = load_migration(REVISION)
    present = (
        "SELECT count(*) = 2 FROM information_schema.columns "
        "WHERE table_name = 'sales_draft_notices' AND column_name IN ('tries', 'due_at')"
    )
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = bool(connection.execute(text(present)).scalar())
        migration.upgrade()
    return down, bool(connection.execute(text(present)).scalar())


async def test_retry_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_columns_down_and_up) == (False, True)
