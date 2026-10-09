"""Стыки агента и продаж — ревью стыков 08.10, пункты C1, C4, C5 и G3.

Что держится и закреплено здесь тестом:

- **C1, тумблер выключен** (`SALES_AGENT_ENABLED`, по умолчанию): ответу лида черновик не
  положен — словами, без вызова модели и без сообщения в группу; экран настроек агента этапа
  продаж не показывает, правка его настроек — 404 словами, «написать заново» — 409 словами;
- **G3**: модуль продаж подключён к мосту почты в каждом процессе, где почта может спросить о
  письме продаж: API, воркер, воркер продаж, добивки, сторож, консоль;
- **C4**: бот продаж без токена — Telegram не зовётся ни разу, строка «не доставлено» с
  названием настройки, своей тревоги у черновика нет (сводная — `test_sales_bot_token_alarm.py`),
  черновик цел;
- **C1, тумблер включён**: ответ лида с видом «вопрос» или «интересуется» (путь «ответит
  агент») получает задачу черновика агента — её ставит задача продаж (`workers/sales_jobs.py`,
  PR «общее: ответ агента продаж»; очередь и пути — `tests/test_sales_reply_draft.py`);
- **ответ лиду из переписки** (`letters/answers.py`) идёт мостом к модулю продаж, когда
  продажи подключены: черновик агента продаж уходит как есть и с правкой (тот же PR;
  настоящий модуль — `tests/test_sales_thread_answer.py`);
- **C5**: автопилот (`agent/autopilot.run`) при «исход неизвестен» (`MaybeSentError`) отдаёт
  черновик человеку с причиной, задача не падает, второго письма нет.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from types import MappingProxyType

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.agent import autopilot, drafting, stages
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import DraftStatus, MessageStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters.answers import answer_reply
from backend.features.letters.sending import Sending
from backend.features.letters.transport import MaybeSentError, NullTransport, Outgoing
from backend.features.replies.pipeline import Inbox
from backend.features.sales.agent import notify, parts
from backend.features.sales.models import NoticeStatus
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.shared import queue as shared_queue
from backend.workers import agent_jobs, sales_jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_agent_autopilot import _accepted, _drafted, _pilot, allowed
from tests.test_agent_drafting import reply, stored
from tests.test_parse_requeue import UniqueQueue
from tests.test_replies_inbox import sent
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import GOOD, INFORM, Writer, lead_replied, sales_on
from tests.test_sales_draft_notify import (
    BotApi,
    alerts,
    drafted,
    journal,
    ok,
    pauses,
    plug,
    queue,
    wired,
)
from tests.test_sales_model import ROOT
from tests.test_sales_reply_routing import NOW as ROUTED
from tests.test_sales_reply_routing import (
    SECRET,
    FakeClassifier,
    _Closable,
    _incoming,
    sales_letter,
)
from tests.test_sales_stage_bridge import (
    ANSWER_TAIL,
    LEAD_EMAIL,
    _seen,
    _transports,
    fake,
    unregistered,
)
from tests.test_sales_stage_mail import NOW, sales_world

__all__ = [  # фикстуры — отсюда их видит pytest
    "alerts",
    "allowed",
    "fake",
    "llm",
    "pauses",
    "queue",
    "reply",
    "sales_on",
    "sent",
    "unregistered",
    "wired",
]

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
OFF = "на этапе «sales» агент переписку не ведёт"


@pytest.fixture
def sales_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Реестр этапов агента без строки продаж — как с выключенным `SALES_AGENT_ENABLED`, даже
    если в окружении разработчика тумблер включён."""
    original = stages.AGENT_STAGES
    registry = MappingProxyType({k: v for k, v in original.items() if k is not Stage.SALES})
    for name, module in list(sys.modules.items()):
        if name.startswith(("backend.", "tests.")) and vars(module).get("AGENT_STAGES") is original:
            monkeypatch.setattr(module, "AGENT_STAGES", registry)


async def _admin(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin@seam-r3.example.test", role=UserRole.ADMIN)
    return bearer(await sign_in("admin@seam-r3.example.test"))


# --- C1: тумблер выключен — ни черновика, ни сообщения, ни расхода ---------------------------


@pytest.mark.usefixtures("sales_off")
async def test_c1_switched_off_the_lead_gets_no_draft_and_no_model_is_called(
    session: AsyncSession, llm: Plug, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply_id = await lead_replied(session)
    model = llm(situation=[INFORM], judge=[{}])
    announced: list[int] = []
    monkeypatch.setattr(notify, "queue_notice", announced.append)

    assert await drafting.wants_draft(session, reply_id) is False  # задачу не ставят вовсе
    outcome = await drafting.draft_answer(session, Writer(GOOD), reply_id, again=True)
    await drafting.announce(outcome)

    assert (outcome.notice, outcome.skipped, outcome.tokens) == (None, OFF, 0)
    assert model.sent == {"situation": [], "judge": []}
    assert announced == []
    assert await session.scalar(select(AgentDraftModel.id)) is None


@pytest.mark.usefixtures("sales_off")
async def test_c1_switched_off_the_screen_and_the_button_say_so_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    reply_id = await lead_replied(session)
    found = await session.get(ReplyModel, reply_id)
    assert found is not None
    await session.commit()
    headers = await _admin(make_user, sign_in)

    shown = await client.get("/api/agent/settings", headers=headers)
    saved = await client.post(
        "/api/agent/settings/sales",
        json={"enabled": True, "goal": "г", "tone": "т", "points": [], "stop_topics": []},
        headers=headers,
    )
    redrafted = await client.post(
        f"/api/threads/{found.thread_id}/replies/{reply_id}/draft", headers=headers
    )

    assert shown.status_code == 200, shown.text
    assert "sales" not in [stage["stage"] for stage in shown.json()["stages"]]
    assert (saved.status_code, saved.json()["detail"]) == (
        404,
        "На этапе «sales» агент переписку не ведёт",
    )
    assert (redrafted.status_code, redrafted.json()["detail"]) == (
        409,
        f"Черновик не написан: {OFF}",
    )


# --- G3: модуль продаж подключён к мосту почты в каждом процессе ---------------------------

#: Процесс → модуль, с которого он начинает работу: точка входа или задача, которую воркер
#: импортирует по пути (`rq` грузит её в процессе задачи).
PROCESSES = {
    "api": "backend.api.main",
    "worker: пачка писем": "backend.workers.send_jobs",
    "worker: разбор ответа и сборка": "backend.workers.jobs",
    "worker: черновик агента": "backend.workers.agent_jobs",
    "worker-sales: ответ лида": "backend.workers.sales_jobs",
    "worker-sales: сообщение о черновике": "backend.features.sales.agent.notify",
    "followups": "backend.workers.followups",
    "reaper": "backend.workers.reaper",
    "консоль": "backend.cli.main",
}


@pytest.mark.parametrize("module", PROCESSES.values(), ids=PROCESSES.keys())
def test_g3_every_process_has_the_sales_module_on_the_mail_bridge(module: str) -> None:
    """Мост почты знает модуль продаж, только если пакет продаж загружен: иначе отправка письма
    продаж, добивка и политика почты отвечают «продажи к почте ещё не подключены», а сторож
    молчит о продажах. Чистый интерпретатор — то, что грузит сам процесс, а не набор тестов."""
    code = f"import {module}\nfrom backend.features.core import stages\nprint(stages.sales_registered())"
    env = {**os.environ, "ACCESS_JWT_SECRET": "x" * 64}  # без секрета приложение не собирается
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )

    assert (done.returncode, done.stdout.strip()) == (0, "True"), done.stderr[-2000:]


# --- C4: бот продаж без токена ----------------------------------------------------------------


@pytest.mark.usefixtures("sales_on", "queue")
async def test_c4_bot_without_a_token_calls_nobody_and_the_draft_stays_without_its_own_alert(
    session: AsyncSession,
    llm: Plug,
    monkeypatch: pytest.MonkeyPatch,
    wired: list[float],
    alerts: list[str],
) -> None:
    api = plug(monkeypatch, BotApi(ok()))
    monkeypatch.setattr(sales_cfg, "TELEGRAM_BOT_TOKEN", "")
    outcome = await drafted(session, llm, Writer(GOOD))
    assert outcome.draft_id is not None

    report = await notify.run_notice(outcome.draft_id)

    assert (report["status"], api.seen, wired) == ("undelivered", [], [])  # ни запроса, ни пауз
    [row] = await journal(session)
    assert row.status == NoticeStatus.UNDELIVERED
    assert "SALES_TELEGRAM_BOT_TOKEN" in (row.error or "")
    assert alerts == [], "тревога без токена — одна сводная, а не на каждый черновик"
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    assert (draft.status, draft.body) == (DraftStatus.DRAFTED, GOOD)


# --- C1: черновик после вида ответа лида ------------------------------------------------------


@pytest.mark.usefixtures("sales_on")
async def test_c1_switched_on_a_lead_question_gets_a_draft_job_after_its_kind(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    letter = await sales_letter(session)
    got = await Inbox(session, now=ROUTED).accept(_incoming(letter, "What does the audit include?"))
    await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
    await session.commit()
    assert got.reply_id is not None
    runs, sales = UniqueQueue(), UniqueQueue()
    # Любая очередь, куда правка поставит черновик, — подставная: ни одна задача не уйдёт в
    # настоящий Redis (по умолчанию — общий).
    for module in (agent_jobs, sales_jobs, shared_queue):
        monkeypatch.setattr(module, "runs_queue", lambda: runs, raising=False)
        monkeypatch.setattr(module, "sales_queue", lambda: sales, raising=False)
    question = KindFound(SalesKind.QUESTION, 0.93, quote="What does the audit include?")
    monkeypatch.setattr(sales_jobs, "KindClient", lambda: FakeClassifier(question))
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    report = await sales_jobs.handle(got.reply_id)

    assert report["route"] == "agent"
    queued = [args for job, args, _ in runs.jobs + sales.jobs if job == agent_jobs.DRAFT_JOB]
    assert queued == [(got.reply_id,)]


# --- C5: автопилот и «исход неизвестен» — общий код -----------------------------------------


class Lost(NullTransport):
    """Почта приняла запрос и не ответила: письмо, возможно, ушло."""

    async def send(self, outgoing: Outgoing) -> str:
        raise MaybeSentError(
            f"Почтовая платформа не ответила — письмо №{outgoing.message_id} могло уйти"
        )


@pytest.mark.usefixtures("allowed")
async def test_c5_unknown_outcome_of_the_autopilot_letter_goes_to_a_human(
    session: AsyncSession, reply: ReplyModel, sent: MessageModel
) -> None:
    await _accepted(session, sent)
    await _pilot(session)
    draft_id = await _drafted(session, reply, "Thanks! We can pay $120 for a guide.")

    flown = await autopilot.run(session, Sending(session, Lost()), draft_id)

    assert flown.sent_message_id is None
    assert "исход отправки неизвестен" in (flown.held or "")
    draft = await stored(session, reply.id)
    assert draft.status is DraftStatus.ESCALATED
    answer = await session.scalar(
        select(MessageModel).where(MessageModel.answers_reply_id == reply.id)
    )
    assert answer is not None
    assert answer.status is MessageStatus.SENDING  # не в очередь: второго письма не будет


# --- ответ лиду из переписки ----------------------------------------------------------------


@pytest.mark.usefixtures("filled_legal", "fake")
async def test_answer_to_a_lead_goes_through_the_bridge_when_sales_are_connected(
    session: AsyncSession,
) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)
    transports = _transports()
    body = "Thank you. I can walk you through it on a short call."

    await answer_reply(
        session,
        Sending(session, transports, now=NOW),
        thread_id=world.thread.id,
        reply_id=world.reply.id,
        body=body,
        author_id=None,
    )

    [letter] = _seen(transports)
    assert letter.to == LEAD_EMAIL
    assert letter.body == f"{body}{ANSWER_TAIL}"  # текст ответа — тот, что отдал модуль
