"""Черновик агента продаж после вида ответа лида — задача продаж (`workers/sales_jobs.py`).

Тело задачи — на настоящей базе, вид ответа подменён. После записи вида и передачи лида
задача ставит черновик агента шовом (`agent_jobs.after_parse`):

- вопрос и интерес (путь «ответит агент», `replies.DRAFTED`) — одной задачей в очереди
  продаж (`sales`, worker-sales), а не в общей: там черновик ждал бы часовой прогон доноров;
- «хочет говорить» — без черновика: лида передают телемаркетологу (решение ждёт владельца);
  другие пути и уверенность ниже порога — без черновика;
- агент продаж выключен тумблером или не настроен — ни задачи, ни вызова модели;
- лиду, которому не пишут (у телемаркетолога, снят, в стоп-листе), черновика нет — то же
  правило, что у отправки (`mail.unwritable`), а почему — словами в снимке ответа;
- сбой постановки задачу ответа не роняет: вид записан, черновик попросят кнопкой.

Очереди — подставные во всех модулях, где их берут; Redis из настроек — закрытый порт:
задача мимо подставной очереди не дойдёт до общего Redis машины.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import storage
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies.pipeline import Inbox
from backend.features.sales.agent import parts
from backend.features.sales.models import LeadStatus, SalesHandoffModel, SalesStoplistModel
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.shared import queue as shared_queue
from backend.workers import agent_jobs, jobs, sales_jobs
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.test_parse_requeue import UniqueQueue
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_reply_routing import (
    NOW,
    SECRET,
    FakeClassifier,
    _Closable,
    _incoming,
    sales_letter,
)
from tests.test_sales_seam_agent import sales_off
from tests.test_sales_send import JANE, _first_sent, _lead_of

__all__ = ["llm", "sales_off", "sales_on"]  # фикстуры — отсюда их видит pytest

QUESTION = KindFound(SalesKind.QUESTION, 0.93, quote="What does the audit include?")

Answered = Callable[..., Awaitable[tuple[int, dict[str, Any]]]]
Asked = Callable[[str], Awaitable[tuple[int, dict[str, Any]]]]


class Queues:
    """Обе очереди задач — подставные: общая (`runs`) и продаж (`sales`)."""

    def __init__(self) -> None:
        self.runs = UniqueQueue()
        self.sales = UniqueQueue()

    def drafts(self) -> dict[str, list[int]]:
        """Какие ответы получили задачу черновика — по очередям."""
        return {
            name: [args[0] for job, args, _ in queue.jobs if job == agent_jobs.DRAFT_JOB]
            for name, queue in (("runs", self.runs), ("sales", self.sales))
        }


@pytest.fixture
def queues(monkeypatch: pytest.MonkeyPatch) -> Queues:
    found = Queues()
    monkeypatch.setattr(storage, "REDIS_URL", "redis://127.0.0.1:1/0")
    for module in (agent_jobs, jobs, sales_jobs, shared_queue):
        monkeypatch.setattr(module, "runs_queue", lambda: found.runs, raising=False)
        monkeypatch.setattr(module, "sales_queue", lambda: found.sales, raising=False)
    return found


@pytest.fixture
def lead_answered(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Answered:
    """Лид продаж ответил; задача продаж разбирает ответ видом `found` — на базе теста.
    `configured` — настройки агента продаж записаны (по умолчанию, включён)."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    async def answered(found: KindFound, *, configured: bool = True) -> tuple[int, dict[str, Any]]:
        letter = await sales_letter(session)
        got = await Inbox(session, now=NOW).accept(_incoming(letter, found.quote or "Hello."))
        if configured:
            await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
        await session.commit()
        assert got.reply_id is not None
        monkeypatch.setattr(sales_jobs, "KindClient", lambda: FakeClassifier(found))
        return got.reply_id, await sales_jobs.handle(got.reply_id)

    return answered


# --- вопрос и интерес — черновик в очереди продаж -------------------------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize("kind", [SalesKind.QUESTION, SalesKind.INTERESTED])
async def test_question_and_interest_get_one_draft_job_in_the_sales_queue(
    queues: Queues, lead_answered: Answered, kind: SalesKind
) -> None:
    reply_id, report = await lead_answered(KindFound(kind, 0.93, quote="Tell me more, please."))

    assert report["route"] == "agent"
    assert queues.drafts() == {"runs": [], "sales": [reply_id]}
    [(_, _, options)] = queues.sales.jobs
    assert (options["job_id"], options["unique"]) == (f"draft-reply-{reply_id}", True)


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize(
    ("found", "route"),
    [
        (KindFound(SalesKind.WANTS_TO_TALK, 0.95, quote="Let us talk on Tuesday."), "handoff"),
        (KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="Not for us, thanks."), "closed"),
        (KindFound(SalesKind.QUESTION, 0.31, quote="What is it about?"), "manual"),
    ],
    ids=["wants-to-talk", "not-interested", "question-below-the-threshold"],
)
async def test_other_routes_get_no_draft(
    queues: Queues, lead_answered: Answered, found: KindFound, route: str
) -> None:
    """«Хочет говорить» — передача телемаркетологу, черновика нет (сменить —
    `replies.DRAFTED`); вопрос ниже порога уверенности — путь человека, не вид."""
    _, report = await lead_answered(found)

    assert report["route"] == route
    assert queues.drafts() == {"runs": [], "sales": []}


# --- без агента продаж — ни задачи, ни модели -----------------------------------------------


@pytest.mark.parametrize("how", ["switched-off", "not-configured"])
async def test_without_the_sales_agent_there_is_no_draft_and_no_model(
    request: pytest.FixtureRequest,
    queues: Queues,
    lead_answered: Answered,
    llm: Plug,
    how: str,
) -> None:
    """Тумблер `SALES_AGENT_ENABLED` выключен (строки продаж в реестре этапов нет) или агент
    продаж не настроен — задача ответа черновик не ставит и модели агента не зовёт."""
    request.getfixturevalue("sales_off" if how == "switched-off" else "sales_on")
    model = llm(situation=[], judge=[])

    _, report = await lead_answered(QUESTION, configured=how != "not-configured")

    assert report["route"] == "agent"
    assert queues.drafts() == {"runs": [], "sales": []}
    assert model.sent == {"situation": [], "judge": []}


# --- сбой постановки — задача ответа цела ---------------------------------------------------


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize("trouble", ["choice-fails", "queue-down"])
async def test_a_failure_to_queue_the_draft_does_not_fail_the_reply_job(
    session: AsyncSession,
    queues: Queues,
    lead_answered: Answered,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    trouble: str,
) -> None:
    """Вид уже записан: сбой выбора черновика или лежащая очередь — строка в журнал, итог
    задачи — вид и путь, а не падение и повтор («уже разобран» вместо разбора)."""
    if trouble == "choice-fails":

        async def broken(*_: object) -> bool:
            raise RuntimeError("выдуманный сбой выбора черновика")

        monkeypatch.setattr(agent_jobs.drafting, "wants_draft", broken)
    else:
        queues.sales.down = True

    with caplog.at_level(logging.WARNING, logger=agent_jobs.__name__):
        reply_id, report = await lead_answered(QUESTION)

    assert (report["kind"], report["route"]) == ("question", "agent")
    reply = await session.get(ReplyModel, reply_id)
    assert reply is not None
    await session.refresh(reply)
    assert (reply.model_parse or {}).get("kind") == "question"
    assert queues.drafts() == {"runs": [], "sales": []}
    said = {
        "choice-fails": f"ответ №{reply_id} разобран, а черновик не поставлен",
        "queue-down": f"черновик ответа №{reply_id} не поставлен — очередь недоступна",
    }
    assert said[trouble] in caplog.text


# --- лиду, которому не пишут, черновика нет ---------------------------------------------------


@pytest.fixture
def asked(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Asked:
    """Подключённые продажи: первое письмо ушло лиду общей отправкой, лид задал вопрос, задача
    продаж разбирает ответ — на базе теста. `how` — что с лидом к этому часу."""
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(sales_jobs, "KindClient", lambda: FakeClassifier(QUESTION))

    async def lead_asked(how: str) -> tuple[int, dict[str, Any]]:
        world = await w.world(session, monkeypatch)
        _, [first] = await _first_sent(session, world)
        lead = await _lead_of(session, first)
        if how == "handed-off":
            session.add(SalesHandoffModel(thread_id=first.thread_id, lead_id=lead.id))
        elif how == "removed":
            lead.status = LeadStatus.REJECTED
        elif how == "stop-list":
            session.add(SalesStoplistModel(email=lead.email))
        reply = ReplyModel(
            thread_id=first.thread_id,
            message_id=first.id,
            kind=ReplyKind.HUMAN,
            raw_body=QUESTION.quote or "",
            from_email=lead.email,
            subject=f"Re: {first.subject}",
            inbound_message_id="<in-7@acme.example.test>",
        )
        session.add(reply)
        await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
        await session.commit()
        return reply.id, await sales_jobs.handle(reply.id)

    return lead_asked


@pytest.mark.usefixtures("sales_on")
@pytest.mark.parametrize(
    ("how", "why"),
    [
        ("handed-off", f"лид {JANE} у телемаркетолога — письма продаж ему не идут"),
        ("removed", f"лида {JANE} сняли (сейчас «rejected») — писать ему нельзя"),
        ("stop-list", f"лиду {JANE} писать нельзя: в ручном стоп-листе продаж"),
    ],
    ids=["handed-off", "removed", "stop-list"],
)
async def test_a_lead_that_must_not_be_written_gets_no_draft_and_the_reason_in_words(
    session: AsyncSession, queues: Queues, asked: Asked, how: str, why: str
) -> None:
    """Отправку такого черновика отбил бы `mail._writable`, а модель заплатила бы за письмо,
    которое не уйдёт. Правило то же (`mail.unwritable`); путь прежний — ответ ждёт человека."""
    reply_id, report = await asked(how)

    reason = f"задал вопрос: {why}; черновика агента нет — решает человек"
    assert (report["route"], report["waits"], report["reason"]) == ("agent", True, reason)
    assert queues.drafts() == {"runs": [], "sales": []}
    reply = await session.get(ReplyModel, reply_id)
    assert reply is not None
    await session.refresh(reply)
    assert (reply.model_parse or {}).get("reason") == reason


@pytest.mark.usefixtures("sales_on")
async def test_a_lead_that_may_be_written_gets_the_draft_as_before(
    queues: Queues, asked: Asked
) -> None:
    reply_id, report = await asked("writable")

    assert report["reason"] == "задал вопрос: ответит агент; пока — человек"
    assert queues.drafts() == {"runs": [], "sales": [reply_id]}
