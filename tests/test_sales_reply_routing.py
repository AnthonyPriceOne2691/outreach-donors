"""Ответ продаж уходит своей очереди — срез 2.1 (`sales-reply-routing`).

Приём общий с донорами. Ответ человека в треде продаж не разбирается как цена
и не становится лидом рекламодателя: он уходит задачей `SALES_REPLY_JOB`
в очередь `sales`, которую слушает свой воркер (`worker-sales`), — часовой
прогон доноров в общей очереди его не держит (A2). Автоответ и отказ доставки
решают правила приёма, как у всех этапов (A4). Redis лежит — 503, и повтор
вебхука ставит задачу (A3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from backend.api.inbound import routes as inbound_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    Stage,
    ThreadStatus,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.ops import job_outcome
from backend.features.outreach.threads import ThreadState, summarize
from backend.features.replies import outcome
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind, Unanswered
from backend.shared import queue
from backend.shared.sliding_window import SlidingWindow
from backend.workers import health, sales_jobs
from backend.workers import main as worker_main
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_parse_requeue import UniqueQueue
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: путь вида ответа и передачи лида

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SECRET = "r" * 32
HOST = "lead-routing.example"
URL = "/api/inbound/replies"
AUTH = {"X-Inbound-Secret": SECRET}
MESSAGE_ID = f"<in-1@{HOST}>"


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подпись адреса ответа — выдуманным секретом: им же подписано письмо."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.fixture
def queues(monkeypatch: pytest.MonkeyPatch) -> dict[str, UniqueQueue]:
    """Обе очереди вебхука — подставные: общая (`runs`) и продаж (`sales`)."""
    monkeypatch.setattr(inbound_routes, "_throttle", SlidingWindow())
    fakes = {queue.QUEUE_NAME: UniqueQueue(), queue.SALES_QUEUE_NAME: UniqueQueue()}
    monkeypatch.setattr(inbound_routes, "runs_queue", lambda: fakes[queue.QUEUE_NAME])
    monkeypatch.setattr(inbound_routes, "sales_queue", lambda: fakes[queue.SALES_QUEUE_NAME])
    return fakes


async def sales_letter(session: AsyncSession, stage: Stage = Stage.SALES) -> MessageModel:
    """Отправленное письмо этапа и его переписка; срок добивки не погашен."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=stage, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    contact = ContactModel(domain_id=domain.id, email=f"ceo@{HOST}", source=ContactSource.MANUAL)
    session.add(contact)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="A short question",
        body="Hi,\n\nshort question.",
        sent_at=NOW,
        next_action_at=NOW.replace(day=13),
        internet_message_id=f"<ours-1@{HOST}>",
        idempotency_key=f"{stage.value}:{HOST}:0",
    )
    session.add(letter)
    await session.commit()
    return letter


def _form(letter: MessageModel, text: str, **extra: str) -> dict[str, str]:
    return {
        "from": f"Lead <ceo@{HOST}>",
        "to": reply_to.address_for(letter.id, sender_email="sales@mail.example", secret=SECRET),
        "subject": "Re: A short question",
        "text": text,
        "headers": f"Message-ID: {MESSAGE_ID}",
        **extra,
    }


def _incoming(letter: MessageModel, text: str, **extra: Any) -> Incoming:
    return Incoming(
        message_id=MESSAGE_ID,
        to=(reply_to.address_for(letter.id, sender_email="sales@mail.example", secret=SECRET),),
        from_email=f"ceo@{HOST}",
        subject="Re: A short question",
        text=text,
        **extra,
    )


class FakeClassifier:
    """Вид ответа без модели: отдаёт заданное и считает вызовы."""

    model = "fake-model"

    def __init__(self, found: KindFound | Unanswered | None = None) -> None:
        self.found = found or KindFound(SalesKind.INTERESTED, 0.9, quote="Tell me more")
        self.calls = 0

    async def classify(self, *, text: str, subject: str) -> KindFound | Unanswered:
        self.calls += 1
        return self.found

    async def aclose(self) -> None:
        return None


async def _reply(session: AsyncSession) -> ReplyModel:
    return (await session.execute(select(ReplyModel))).scalars().one()


# --- A1: ответ человека — в очередь продаж ------------------------------------------------


async def test_a1_human_answer_goes_to_the_sales_queue_not_to_price_or_lead(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    letter = await sales_letter(session)

    response = await client.post(
        URL, data=_form(letter, "We pay $300 a month today. Call me tomorrow."), headers=AUTH
    )

    assert response.status_code == 200, response.text
    reply = await _reply(session)
    assert reply.kind is ReplyKind.HUMAN
    assert queues[queue.QUEUE_NAME].jobs == [], "разбора цены нет"
    [(job, args, options)] = queues[queue.SALES_QUEUE_NAME].jobs
    assert (job, args) == (queue.SALES_REPLY_JOB, (reply.id,))
    assert options["job_id"] == queue.sales_job_id(reply.id, MESSAGE_ID)
    assert options["unique"] is True
    assert options["retry"].max == len(queue.RETRY_INTERVALS)
    body = response.json()
    assert (body["needs_review"], body["reason"]) == (True, outcome.SALES_WAITING)
    # «Лида рекламодателя» нет: диалог — в своём состоянии продаж, цены нет.
    summary = summarize([letter], [reply], Stage.SALES)
    assert summary.state is ThreadState.SALES_PENDING
    assert summary.price_white is None


async def test_a1_inbox_hands_the_answer_to_sales_and_stops_the_chain(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)

    got = await Inbox(session, now=NOW).accept(_incoming(letter, "Tell me more, please."))

    assert (got.to_sales, got.to_parse) == (got.reply_id, None)
    assert (got.sales_pending, got.parse_pending) == (True, False)
    assert got.as_report["продажам"] == got.reply_id
    await session.flush()
    await session.refresh(letter)
    assert letter.next_action_at is None, "ответил человек — добивок нет"
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.REPLIED


@pytest.mark.parametrize(
    ("stage", "kind", "to_sales"),
    [
        (Stage.SALES, ReplyKind.HUMAN, True),
        # 2.3: автоответ переносит шаг продаж, отписка закрывает адрес — без модели.
        (Stage.SALES, ReplyKind.AUTO_REPLY, True),
        (Stage.SALES, ReplyKind.BOUNCE, False),
        (Stage.SALES, ReplyKind.UNSUBSCRIBE, True),
        (Stage.DONORS, ReplyKind.HUMAN, False),
        (Stage.ADVERTISERS, ReplyKind.HUMAN, False),
        (None, ReplyKind.HUMAN, False),
    ],
)
def test_only_answers_of_a_sales_thread_go_to_sales_and_never_a_bounce(
    stage: Stage | None, kind: ReplyKind, to_sales: bool
) -> None:
    assert outcome.to_sales_queue(kind, stage) is to_sales


async def test_donor_answer_still_goes_to_the_price_parse(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    letter = await sales_letter(session, Stage.DONORS)

    response = await client.post(URL, data=_form(letter, "Our rate is 150 USD."), headers=AUTH)

    assert response.status_code == 200, response.text
    assert [job for job, _, _ in queues[queue.QUEUE_NAME].jobs] == [queue.PARSE_JOB]
    assert queues[queue.SALES_QUEUE_NAME].jobs == []


# --- A2: свой воркер, своя очередь ------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "listens"),
    [([], [queue.QUEUE_NAME]), (["--queue", "sales"], [queue.SALES_QUEUE_NAME])],
)
def test_a2_worker_listens_to_the_queue_it_is_given_with_a_scheduler(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], listens: list[str]
) -> None:
    started: dict[str, Any] = {}

    class _Worker:
        def __init__(self, queues: list[str], *, connection: Any) -> None:
            started["queues"] = queues

        def work(self, **kwargs: Any) -> None:
            started.update(kwargs)

    monkeypatch.setattr(worker_main, "Worker", _Worker)
    monkeypatch.setattr(worker_main, "check_storage", lambda: None)
    monkeypatch.setattr(worker_main, "connection", lambda: None)

    worker_main.main(argv)

    assert started == {"queues": listens, "with_scheduler": True}


def test_a2_worker_refuses_a_queue_nobody_fills() -> None:
    with pytest.raises(SystemExit):
        worker_main.queue_of(["--queue", "crawl-typo"])


def test_a2_sales_queue_is_its_own() -> None:
    built = queue.sales_queue(queue.connection())
    assert built.name == queue.SALES_QUEUE_NAME == "sales"
    assert built._default_timeout == queue.JOB_TIMEOUT
    assert queue.sales_job_id(5, MESSAGE_ID) == f"sales-{queue.parse_job_id(5, MESSAGE_ID)}"


def test_a2_compose_runs_a_separate_sales_worker_that_restore_stops() -> None:
    """Сервис `worker-sales` — свой процесс со своей очередью и проверкой здоровья;
    лимиты — на проде; восстановление базы его останавливает."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore.sh").read_text(encoding="utf-8")

    service = compose["services"]["worker-sales"]
    assert service["command"] == "python -m backend.workers.main --queue sales"
    assert service["healthcheck"]["test"][-1] == "sales"
    assert "deploy" not in service, "один процесс воркера"
    assert compose["services"]["worker"]["command"] == "python -m backend.workers.main"
    # Боевой файл с тегами компоуза (`!override`) — YAML без них не читается.
    limits = prod.split("\n  worker-sales:\n", 1)[1].split("\n\n", 1)[0]
    assert "mem_limit: 1536m" in limits
    assert "cpus: 1.0" in limits
    writers = next(line for line in restore.splitlines() if line.startswith("WRITERS="))
    assert " worker-sales " in writers


def test_a2_health_checks_the_sales_worker_against_its_own_queue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []

    def problems(redis: object, queue_name: str) -> list[str]:
        asked.append(queue_name)
        return [] if queue_name == "sales" else ["не та очередь"]

    monkeypatch.setattr(health, "worker_problems", problems)
    monkeypatch.setattr(health, "connection", lambda: None)

    assert health.main(["sales"]) == 0
    assert asked == ["sales"]
    assert capsys.readouterr().out.strip() == "здоров"


async def test_a2_the_job_body_takes_the_answer_without_the_runs_queue(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тело задачи — на настоящей базе: вид разобран, ответ ведён по пути; общая
    очередь не тронута — задача продаж её не ждёт и в неё не ставит."""
    letter = await sales_letter(session)
    got = await Inbox(session, now=NOW).accept(_incoming(letter, "Tell me more, please."))
    await session.commit()
    assert got.reply_id is not None

    def no_runs_queue() -> None:
        raise AssertionError("задача продаж не трогает общую очередь")

    monkeypatch.setattr(queue, "runs_queue", no_runs_queue)
    monkeypatch.setattr(sales_jobs, "KindClient", FakeClassifier)
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        sales_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    report = await sales_jobs.handle(got.reply_id)

    assert report == {
        "reply": got.reply_id,
        "kind": "interested",
        "route": "agent",
        "waits": True,
        "reason": "интересуется: ответит агент; пока — человек",
        "tokens": 0,
    }
    assert job_outcome.KINDS[queue.SALES_REPLY_JOB] == "разбор ответа продаж"


class _Closable:
    async def dispose(self) -> None:
        return None


async def test_job_does_not_take_what_is_not_a_fresh_sales_answer(session: AsyncSession) -> None:
    """Задача живёт дольше кода: к чужому, удалённому или решённому ответу — итог словами."""
    donor = await sales_letter(session, Stage.DONORS)
    donor_reply = await Inbox(session, now=NOW).accept(_incoming(donor, "Our rate is $90."))
    model = FakeClassifier()
    sales = SalesReplies(session, model)

    assert (await sales.handle(10**9)).skipped == "ответа нет: удалён до разбора"
    assert donor_reply.reply_id is not None
    assert (await sales.handle(donor_reply.reply_id)).skipped == "не ответ продаж"
    reply = await session.get(ReplyModel, donor_reply.reply_id)
    assert reply is not None
    reply.reviewed_at = NOW
    assert (await sales.handle(reply.id)).skipped == "решён человеком"
    assert model.calls == 0, "чужой ответ модели не отдаётся"


# --- A3: Redis лежит — 503, повтор ставит задачу -------------------------------------------


async def test_a3_queue_down_means_503_and_the_retry_queues_the_sales_job(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    letter = await sales_letter(session)
    sales = queues[queue.SALES_QUEUE_NAME]
    sales.down = True

    first = await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)

    assert first.status_code == 503, first.text
    assert "разбор ответа продаж не поставлен" in first.json()["reason"]
    reply = await _reply(session)
    assert sales.jobs == []

    sales.down = False
    again = await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)
    third = await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)

    assert (again.status_code, third.status_code) == (200, 200)
    assert again.json()["duplicate"] is True
    assert [(job, args) for job, args, _ in sales.jobs] == [(queue.SALES_REPLY_JOB, (reply.id,))]
    assert queues[queue.QUEUE_NAME].jobs == []


async def test_a3_retry_after_the_answer_was_sorted_queues_nothing(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    letter = await sales_letter(session)
    await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)
    reply = await _reply(session)
    reply.model_parse = {"stage": "sales", "kind": "interested"}
    await session.commit()
    queues[queue.SALES_QUEUE_NAME].jobs.clear()

    again = await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)

    assert again.status_code == 200
    assert queues[queue.SALES_QUEUE_NAME].jobs == []


# --- A4: автоответ и отказ доставки — правила приёма, без модели ------------------------------


async def test_a4_auto_reply_in_a_sales_thread_is_decided_by_the_rules(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    """Вид — по правилам приёма, цепочка не остановлена; задача продаж — только
    чтобы перенести следующий шаг (2.3), модель автоответу не отдаётся."""
    letter = await sales_letter(session)

    response = await client.post(
        URL,
        data=_form(
            letter,
            "I am out of the office until Monday with limited access to email.",
            headers=f"Message-ID: {MESSAGE_ID}\nAuto-Submitted: auto-replied",
        ),
        headers=AUTH,
    )

    assert response.status_code == 200, response.text
    reply = await _reply(session)
    assert reply.kind is ReplyKind.AUTO_REPLY
    assert [args for _, args, _ in queues[queue.SALES_QUEUE_NAME].jobs] == [(reply.id,)]
    assert queues[queue.QUEUE_NAME].jobs == []
    model = FakeClassifier()
    await SalesReplies(session, model).handle(reply.id)
    assert model.calls == 0
    await session.refresh(letter)
    assert letter.next_action_at is not None, "автоответ цепочку не останавливает"


async def test_a4_bounce_in_a_sales_thread_marks_the_address_without_the_model(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    bounce = Incoming(
        message_id=MESSAGE_ID,
        to=_incoming(letter, "").to,
        from_email="mailer-daemon@googlemail.example",
        subject="Delivery Status Notification (Failure)",
        text=f"Delivery to the following recipient failed permanently: ceo@{HOST}",
        headers={"Auto-Submitted": "auto-replied"},
    )

    got = await Inbox(session, now=NOW).accept(bounce)

    assert got.kind is ReplyKind.BOUNCE
    assert (got.to_sales, got.to_parse) == (None, None)
    await session.flush()
    await session.refresh(letter)
    assert letter.status is MessageStatus.BOUNCED
