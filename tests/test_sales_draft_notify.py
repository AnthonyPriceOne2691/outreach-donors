"""Сообщение о черновике агента продаж в группу продаж — срез 3.5, A1 и A2.

Черновик пишется настоящим путём шва (`drafting.draft_answer`) по переписке продаж,
модель ситуации и судьи — подставной HTTP, писатель — подставной. Крючок шва
(`drafting.announce` → `AgentStage.on_draft`) только ставит задачу; задача работает
в транзакции теста, бот продаж — настоящий поверх подставного Bot API
(`httpx.MockTransport`), паузы между попытками не спят. Сеть не ходит, очереди нет.
Номера чатов, токен и адреса — выдуманные. Строка продаж в реестре этапов —
фикстурой `sales_on`, как тумблером `SALES_AGENT_ENABLED`.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any

import httpx
import pytest
import rq
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import sales as cfg
from backend.features.agent import drafting
from backend.features.agent.drafting import DraftOutcome
from backend.features.agent.stages import SALES_STAGE, DraftNotice
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales import telegram
from backend.features.sales.agent import notify, parts
from backend.features.sales.models import NoticeStatus, SalesDraftNoticeModel
from backend.features.sales.telegram import SalesBot
from backend.shared import queue as shared_queue
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.migration_helpers import load_migration
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import BAD, GOOD, INFORM, Writer, lead_replied, sales_on
from tests.test_sales_model import ROOT
from tests.test_sales_stage_mail import LEAD
from tests.test_sales_switch import sales_switched_on

__all__ = [  # фикстуры — отсюда их видит pytest
    "llm",
    "sales_on",
    "sales_switched_on",  # продажи включены (SALES_ENABLED): при выключенных сообщения нет
]

pytestmark = pytest.mark.usefixtures("sales_on")

TOKEN = "6209481735:AAH-notice_token-for-tests"  # pragma: allowlist secret
GROUP = "-1004719305286"
APP = "https://app.example.test"
ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}
NOTICES_REVISION = "d64e2cd71614_sales_draft_notices.py"
#: Очередь, которую модуль сообщения взял при импорте, — до страховки набора
#: (`conftest._no_sales_notice_in_a_real_queue` подменяет её в каждом тесте).
IMPORTED_QUEUE = notify.sales_queue


class BotApi:
    """Подставной Bot API: отвечает по очереди, последний ответ повторяет, помнит запросы."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.seen: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(json.loads(request.content))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 73}})


def down() -> httpx.Response:
    return httpx.Response(503, json={"ok": False, "error_code": 503, "description": "Unavailable"})


class Queue:
    """Очередь на месте Redis: помнит, что поставили."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, path: str, *args: Any, **_options: Any) -> None:
        self.jobs.append((path, args))


class _Closable:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(telegram, "_sleep", sleep)
    return slept


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги эксплуатации — в список: бот тревог в тестах не ходит в сеть."""
    raised: list[str] = []

    async def alert(text: str) -> bool:
        raised.append(text)
        return True

    monkeypatch.setattr(notify, "send_alert", alert)
    return raised


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Queue:
    placed = Queue()
    monkeypatch.setattr(notify, "sales_queue", lambda: placed)
    return placed


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, pauses: list[float]
) -> list[float]:
    """Задача работает в транзакции теста; бот продаж и группа заданы."""
    monkeypatch.setattr(notify, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        notify,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(cfg, "APP_URL", APP)
    return pauses


def plug(monkeypatch: pytest.MonkeyPatch, api: BotApi) -> BotApi:
    monkeypatch.setattr(notify, "_http", api.client)
    return api


async def drafted(
    session: AsyncSession, llm: Plug, writer: Writer, judge: list[Any] | None = None
) -> DraftOutcome:
    """Черновик продаж путём шва — и объявление крючком после коммита, как в задаче."""
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=judge or [ALLOW])
    outcome = await drafting.draft_answer(session, writer, reply_id)
    await session.commit()
    await drafting.announce(outcome)
    return outcome


async def journal(session: AsyncSession) -> list[SalesDraftNoticeModel]:
    rows = await session.scalars(select(SalesDraftNoticeModel).order_by(SalesDraftNoticeModel.id))
    return list(rows)


def thread_of(text: str) -> str:
    return text.rsplit("/threads/", 1)[1]


# --- A1: новый черновик → сообщение со ссылкой, строка «отправлено» ---------------------


async def test_a1_new_draft_is_announced_with_a_link_and_a_sent_row(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    alerts: list[str],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.status is DraftStatus.DRAFTED
    assert outcome.draft_id is not None
    assert queue.jobs == [(notify.NOTICE_JOB, (outcome.draft_id,))]
    assert api.seen == []  # крючок шва в Telegram не ходит — только ставит задачу

    report = await notify.run_notice(outcome.draft_id)

    assert report == {"draft": outcome.draft_id, "status": "sent", "skipped": None, "error": None}
    [sent] = api.seen
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    assert sent["chat_id"] == GROUP
    lines = sent["text"].split("\n")
    assert lines[0] == "Продажи: черновик ответа готов — проверьте и отправьте"
    assert lines[1] == f"Кому: ceo@{LEAD} ({LEAD})"
    assert lines[2] == "О чём: Re: A question about your team"
    assert lines[3] == "Ситуация: asks_info"
    assert lines[4] == "Ход: inform · письмо собеседника №1"
    assert lines[5] == "Судья: пропустил"
    assert lines[6].startswith(f"Переписка: {APP}/threads/")
    [row] = await journal(session)
    assert (row.draft_id, row.status, row.error) == (outcome.draft_id, NoticeStatus.SENT, None)
    assert row.text == sent["text"]
    assert row.written_at == draft.updated_at
    assert (wired, alerts) == ([], [])


async def test_link_leads_to_the_thread_of_the_draft(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.notice is not None

    await notify.run_notice(outcome.notice.draft_id)

    assert thread_of(api.seen[0]["text"]) == str(outcome.notice.thread_id)


async def test_escalated_draft_says_who_held_it_and_why(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(BAD))
    assert outcome.status is DraftStatus.ESCALATED
    assert outcome.draft_id is not None

    await notify.run_notice(outcome.draft_id)

    lines = api.seen[0]["text"].split("\n")
    assert lines[0] == "Продажи: ответ ждёт человека — как есть агент его не отправит"
    assert lines[5].startswith(
        "Судья: не пропустил, правок: 3 — человеку: судья не пропустил черновик и после 3 правок"
    )


# --- A2: Telegram недоступен → 3 попытки, повтор проходом, черновик цел --------------------


async def test_a2_telegram_down_three_attempts_then_a_pass_retry_and_the_draft_stays(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    alerts: list[str],
) -> None:
    """Не ушло из-за 5xx: строка «не доставлено» со сроком повтора прохода (решение владельца —
    та же серия, что у сообщения о лиде); до последней попытки тревоги нет
    (`test_sales_draft_notice_retry.py`)."""
    api = plug(monkeypatch, BotApi(down()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    before = await session.get(AgentDraftModel, outcome.draft_id)
    assert before is not None
    status, body = before.status, before.body

    report = await notify.run_notice(outcome.draft_id)

    assert len(api.seen) == 3
    assert wired == [2.0, 5.0]
    [row] = await journal(session)
    assert (row.status, row.tries) == (NoticeStatus.UNDELIVERED, 1)
    assert row.due_at is not None
    assert row.error is not None
    assert row.error.startswith("не доставлено за 3 попытки: Telegram отказал (HTTP 503")
    assert row.error.endswith("(попытка 1 из 5)")
    assert report["status"] == "undelivered"
    assert alerts == [], "до последней попытки тревоги нет"
    assert TOKEN not in row.error
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    await session.refresh(draft)
    assert (draft.status, draft.body, draft.decided_at) == (status, body, None)


async def test_network_failure_is_retried_too_and_nothing_leaks_the_token(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    alerts: list[str],
) -> None:
    api = plug(monkeypatch, BotApi(httpx.ConnectError(f"https://api/bot{TOKEN}/sendMessage")))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None

    await notify.run_notice(outcome.draft_id)

    assert len(api.seen) == 3
    [row] = await journal(session)
    assert row.status == NoticeStatus.UNDELIVERED
    assert row.error is not None
    assert "ConnectError" in row.error
    assert TOKEN not in row.error
    assert (alerts, row.tries) == ([], 1), "сеть — повтор проходом, тревоги ещё нет"


async def test_group_not_set_is_undelivered_with_its_setting_named(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    alerts: list[str],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", "")
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None

    await notify.run_notice(outcome.draft_id)

    assert api.seen == []
    [row] = await journal(session)
    assert row.status == NoticeStatus.UNDELIVERED
    assert "SALES_TELEGRAM_GROUP_CHAT_ID" in (row.error or "")
    assert "SALES_TELEGRAM_GROUP_CHAT_ID" in alerts[0]


async def test_undelivered_version_is_tried_again_by_the_pass_and_its_row_becomes_sent(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    alerts: list[str],
) -> None:
    """Вторая задача о той же версии не перебивает срок повтора: повторяет проход."""
    plug(monkeypatch, BotApi(down()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    await notify.run_notice(outcome.draft_id)
    api = plug(monkeypatch, BotApi(ok()))

    again = await notify.run_notice(outcome.draft_id)
    [row] = await journal(session)
    retried = await notify.notify(
        session,
        outcome.draft_id,
        SalesBot(api.client()),
        alert=notify.send_alert,
        retry=notify.Retry(version=row.written_at, tries=row.tries),
    )

    assert again["skipped"] == "сообщение об этой версии ждёт повтора прохода по расписанию"
    assert retried.status is NoticeStatus.SENT
    await session.refresh(row)
    assert (row.status, row.error, row.tries, row.due_at) == (NoticeStatus.SENT, None, 0, None)
    assert (len(api.seen), alerts) == (1, [])


# --- одна версия — одно сообщение -------------------------------------------------------


async def test_same_version_is_announced_once_and_a_rewrite_is_announced_again(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    await notify.run_notice(outcome.draft_id)

    again = await notify.run_notice(outcome.draft_id)
    rewritten = await drafting.draft_answer(session, Writer(GOOD), outcome.reply_id, again=True)
    await session.commit()
    await drafting.announce(rewritten)
    await notify.run_notice(outcome.draft_id)

    assert again["skipped"] == "об этой версии черновика группе уже сообщено"
    assert rewritten.draft_id == outcome.draft_id
    assert [job for _, (job,) in queue.jobs] == [outcome.draft_id, outcome.draft_id]
    assert len(api.seen) == 2
    first, second = await journal(session)
    assert first.written_at < second.written_at


@pytest.mark.parametrize("decided", [DraftStatus.SENT, DraftStatus.REJECTED, DraftStatus.SKIPPED])
async def test_draft_that_does_not_wait_for_a_human_is_not_announced(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
    decided: DraftStatus,
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    draft.status = decided
    await session.commit()

    report = await notify.run_notice(outcome.draft_id)

    assert report["skipped"] == f"черновик в состоянии «{decided.value}» — человека он не ждёт"
    assert (api.seen, await journal(session)) == ([], [])


async def test_missing_draft_and_other_stage_are_not_announced(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    found = await notify._found(session, outcome.draft_id)
    assert found is not None

    missing = await notify.run_notice(outcome.draft_id + 1000)
    donors = notify._silent(notify._Found(found.draft, Stage.DONORS, found.reply, 1, "x"))

    assert missing["skipped"] == "черновика нет — сообщать не о чем"
    assert donors == "черновик этапа «donors» — группа продаж о нём не знает"
    assert api.seen == []


# --- крючок шва и задача очереди --------------------------------------------------------


def test_suite_never_puts_a_notice_into_a_real_queue(request: pytest.FixtureRequest) -> None:
    """Страховка набора: Redis по умолчанию — общий, задача теста ушла бы чужому воркеру."""
    assert "_no_sales_notice_in_a_real_queue" in request.fixturenames
    with pytest.raises(RedisError, match="настоящую очередь"):
        notify.sales_queue()


def test_notice_goes_to_the_sales_queue_and_its_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очередь продаж (`sales`, воркер `worker-sales`), а не общая: в общей весть о
    черновике ждала бы часовой прогон доноров. Redis не трогается — постановка
    подменена у самой очереди rq, страховка набора снята только здесь."""
    placed: list[tuple[str, str, tuple[Any, ...]]] = []

    def enqueue(self: rq.Queue, path: str, *args: Any, **_options: Any) -> None:
        placed.append((self.name, path, args))

    monkeypatch.setattr(rq.Queue, "enqueue", enqueue)
    monkeypatch.setattr(notify, "sales_queue", IMPORTED_QUEUE)

    notify.queue_notice(41)

    assert placed == [(shared_queue.SALES_QUEUE_NAME, notify.NOTICE_JOB, (41,))]


def test_sales_row_announces_drafts_and_lists_its_reject_reasons() -> None:
    row = SALES_STAGE

    assert row.on_draft is parts.on_draft
    assert row.reject_reasons is parts.REJECT_REASONS


async def test_queue_down_leaves_the_draft_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Queue:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(notify, "sales_queue", broken)
    notice = DraftNotice(
        draft_id=41,
        reply_id=7,
        thread_id=3,
        stage=Stage.SALES,
        status=DraftStatus.DRAFTED,
        reason=None,
    )

    with caplog.at_level(logging.ERROR, logger=notify.__name__):
        await drafting.announce(DraftOutcome(reply_id=7, notice=notice))

    [record] = caplog.records
    assert record.getMessage().startswith("продажи: сообщение о черновике не поставлено")
    assert getattr(record, "draft_id", None) == 41


def test_link_without_the_service_address_names_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "APP_URL", "")

    assert notify.thread_link(12) == "переписка №12 (SALES_APP_URL не задан)"


def test_job_remembers_why_it_failed_and_lets_the_queue_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notify, "setup_logging", lambda: None)
    monkeypatch.setattr(notify, "check_storage", lambda: None)
    remembered: list[tuple[str, str]] = []

    class Job:
        id = "sales-notice-test"

    async def broken(_draft_id: int) -> dict[str, Any]:
        raise RuntimeError("база отвалилась")

    monkeypatch.setattr(notify, "run_notice", broken)
    monkeypatch.setattr(notify, "get_current_job", Job)
    monkeypatch.setattr(
        notify, "remember_job_error", lambda job_id, text: remembered.append((job_id, text))
    )

    with pytest.raises(RuntimeError, match="база отвалилась"):
        notify.notify_draft(5)

    assert remembered == [("sales-notice-test", "RuntimeError: база отвалилась")]


def test_job_runs_the_core_in_its_own_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify, "setup_logging", lambda: None)
    monkeypatch.setattr(notify, "check_storage", lambda: None)

    async def done(draft_id: int) -> dict[str, Any]:
        return {"draft": draft_id}

    monkeypatch.setattr(notify, "run_notice", done)

    assert notify.notify_draft(31) == {"draft": 31}


def test_notice_job_imports_first_in_a_clean_process() -> None:
    """Путь задачи строкой — воркер импортирует его сам, первым."""
    module, _, name = notify.NOTICE_JOB.rpartition(".")
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}; assert callable({module}.{name})"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


# --- журнал: ревизия и каскад ------------------------------------------------------------


def _table_down_and_up(connection: Connection) -> tuple[bool, bool]:
    """Ревизия журнала вниз и вверх на соединении теста: подъём сьюта идёт подпроцессом."""
    migration = load_migration(NOTICES_REVISION)
    present = "SELECT to_regclass('sales_draft_notices') IS NOT NULL"
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = bool(connection.execute(text(present)).scalar())
        migration.upgrade()
    return down, bool(connection.execute(text(present)).scalar())


async def test_notices_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_table_down_and_up) == (False, True)


async def test_notices_go_with_their_draft(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    wired: list[float],
) -> None:
    """Чистка пробного ответа уносит черновик каскадом — и его строки журнала с ним."""
    plug(monkeypatch, BotApi(ok()))
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None
    await notify.run_notice(outcome.draft_id)
    reply = await session.get(ReplyModel, outcome.reply_id)
    assert reply is not None

    await session.delete(reply)
    await session.flush()

    assert await journal(session) == []
