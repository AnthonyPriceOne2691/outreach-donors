"""Разбор цены не теряется, если очередь лежала в минуту вебхука.

Было: ответ коммитился, постановка разбора падала на недоступном Redis,
вебхук отвечал пятисоткой, платформа повторяла письмо — а повтор видел
«уже принято» и не ставил ничего. Ответ навсегда оставался «ждёт разбора».

Стало, и каждое проверено здесь на настоящей базе:

* недоступная очередь — 503 с причиной: ответ сохранён, повтор нужен;
* повтор, заставший ответ без разбора, ставит разбор снова;
* постановка идемпотентна — номер задачи от ответа (rq `unique`), и два
  повтора подряд не дают двух платных разборов;
* задача, пришедшая к уже разобранному ответу, модель не зовёт.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    Stage,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox, Parser
from backend.features.replies.repository import ReplyRepository
from backend.shared.queue import PARSE_JOB, parse_job_id
from backend.shared.sliding_window import SlidingWindow
from backend.workers import jobs
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import DuplicateJobError
from rq.job import JOB_ID_PATTERN
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "travelnotes.co.uk"
URL = "/api/inbound/replies"
AUTH = {"X-Inbound-Secret": SECRET}


class UniqueQueue:
    """Очередь с правилом rq 2.8+: задача с уже занятым номером при
    `unique=True` — отказ `DuplicateJobError`. `down` — Redis лежит."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.down = False

    def enqueue(self, job: str, *args: Any, **options: Any) -> object:
        if self.down:
            raise RedisConnectionError("Error 61 connecting to localhost:6389. Connection refused.")
        taken = {opts.get("job_id") for _, _, opts in self.jobs}
        if options.get("unique") and options.get("job_id") in taken:
            raise DuplicateJobError(f"Job with ID '{options['job_id']}' already exists")
        self.jobs.append((job, args, options))
        return type("Job", (), {"id": options.get("job_id")})()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> UniqueQueue:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    fake = UniqueQueue()
    monkeypatch.setattr(inbound_routes, "runs_queue", lambda: fake)
    monkeypatch.setattr(inbound_routes, "_throttle", SlidingWindow())
    return fake


async def _sent(session: AsyncSession, stage: Stage = Stage.DONORS) -> MessageModel:
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=stage, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        internet_message_id="<ours-1@mail.test>",
        idempotency_key=f"{stage.value}:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def _form(
    message: MessageModel, text: str = "Our sponsored post rate is 150 GBP."
) -> dict[str, str]:
    return {
        "from": f"Elena <editor@{HOST}>",
        "to": reply_to.address_for(message.id, sender_email="anna@mail.test", secret=SECRET),
        "subject": "Re: Advertising rates",
        "text": text,
        "headers": "Message-ID: <in-1@travelnotes.co.uk>",
    }


async def _replies(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(ReplyModel)) or 0)


# --- вебхук -------------------------------------------------------------------


async def test_queue_down_means_503_and_the_reply_is_kept(
    client: AsyncClient, session: AsyncSession, queue: UniqueQueue
) -> None:
    """Не 2xx — чтобы платформа повторила письмо; ответ при этом уже в базе."""
    sent = await _sent(session)
    queue.down = True

    response = await client.post(URL, data=_form(sent), headers=AUTH)

    assert response.status_code == 503, response.text
    assert "разбор цены не поставлен" in response.json()["reason"]
    assert await _replies(session) == 1
    assert queue.jobs == []


async def test_retry_after_an_outage_queues_the_parse(
    client: AsyncClient, session: AsyncSession, queue: UniqueQueue
) -> None:
    """Главный случай: повтор застаёт ответ без разбора и ставит его."""
    sent = await _sent(session)
    queue.down = True
    await client.post(URL, data=_form(sent), headers=AUTH)
    queue.down = False

    again = await client.post(URL, data=_form(sent), headers=AUTH)

    assert again.status_code == 200, again.text
    assert again.json()["duplicate"]
    assert await _replies(session) == 1, "второго ответа повтор не заводит"
    reply_id = int(await session.scalar(select(ReplyModel.id)) or 0)
    assert [(job, args) for job, args, _ in queue.jobs] == [(PARSE_JOB, (reply_id,))]
    options = queue.jobs[0][2]
    assert options["unique"] is True
    assert options["job_id"] == parse_job_id(reply_id, "<in-1@travelnotes.co.uk>")


async def test_two_retries_do_not_queue_two_paid_parses(
    client: AsyncClient, session: AsyncSession, queue: UniqueQueue
) -> None:
    """Приём задачу поставил, а ответ до платформы не дошёл — она повторяет,
    и не раз. Номер задачи тот же, и очередь второй разбор не берёт."""
    sent = await _sent(session)

    first = await client.post(URL, data=_form(sent), headers=AUTH)
    second = await client.post(URL, data=_form(sent), headers=AUTH)
    third = await client.post(URL, data=_form(sent), headers=AUTH)

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert len(queue.jobs) == 1


async def test_retry_after_the_parse_ran_queues_nothing(
    client: AsyncClient, session: AsyncSession, queue: UniqueQueue
) -> None:
    sent = await _sent(session)
    await client.post(URL, data=_form(sent), headers=AUTH)
    reply = (await session.execute(select(ReplyModel))).scalars().one()
    reply.model_parse = Extracted(confidence=0.9).snapshot()
    reply.confidence = 0.9
    await session.commit()
    queue.jobs.clear()

    again = await client.post(URL, data=_form(sent), headers=AUTH)

    assert again.status_code == 200
    assert queue.jobs == []


async def test_retry_of_an_auto_reply_queues_nothing(
    client: AsyncClient, session: AsyncSession, queue: UniqueQueue
) -> None:
    """Автоответ модели не отдаётся ни при приёме, ни при повторе."""
    sent = await _sent(session)
    form = _form(sent, "I am out of the office until Monday.")
    queue.down = True
    await client.post(URL, data=form, headers=AUTH)
    queue.down = False

    again = await client.post(URL, data=form, headers=AUTH)

    assert again.status_code == 200
    assert queue.jobs == []


# --- ответ без разбора: чем он отличается в базе --------------------------------


async def _accepted(session: AsyncSession, stage: Stage = Stage.DONORS) -> ReplyModel:
    sent = await _sent(session, stage)
    got = await Inbox(session, now=NOW).accept(
        Incoming(
            message_id="<in-9@travelnotes.co.uk>",
            to=(reply_to.address_for(sent.id, sender_email="anna@mail.test", secret=SECRET),),
            from_email=f"editor@{HOST}",
            subject="Re: Advertising rates",
            text="Our sponsored post rate is 150 GBP.",
        )
    )
    await session.flush()
    reply = await session.get(ReplyModel, got.reply_id or 0)
    assert reply is not None
    return reply


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.mark.usefixtures("secret")
async def test_unparsed_is_a_donor_reply_without_a_model_snapshot(session: AsyncSession) -> None:
    reply = await _accepted(session)
    repository = ReplyRepository(session)

    assert await repository.parse_never_ran(reply)

    reply.model_parse = Extracted(confidence=0.2).snapshot()
    assert not await repository.parse_never_ran(reply), "разбор шёл — пусть и неуверенно"


@pytest.mark.usefixtures("secret")
async def test_human_decision_replaces_the_parse(session: AsyncSession) -> None:
    """Решённый человеком ответ модели не отдаётся: её разбор затёр бы решение."""
    reply = await _accepted(session)
    reply.reviewed_at = NOW
    reply.reviewed_by = "оператор@site.com"

    assert not await ReplyRepository(session).parse_never_ran(reply)


@pytest.mark.usefixtures("secret")
async def test_advertisers_reply_is_never_parsed(session: AsyncSession) -> None:
    reply = await _accepted(session, Stage.ADVERTISERS)

    assert reply.kind is ReplyKind.HUMAN
    assert not await ReplyRepository(session).parse_never_ran(reply)


# --- задача разбора -----------------------------------------------------------


class CountingExtractor:
    """Платная модель разбора — подменена и считает вызовы."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, incoming: Incoming) -> Extracted:
        self.calls += 1
        return Extracted(
            price_white=Decimal("150"),
            currency="GBP",
            placement="sells",
            confidence=0.95,
            tokens_spent=321,
        )

    async def aclose(self) -> None:
        return None


class _Closable:
    async def dispose(self) -> None:
        return None


@pytest.mark.usefixtures("secret")
async def test_parser_does_not_pay_twice_for_one_reply(session: AsyncSession) -> None:
    reply = await _accepted(session)
    model = CountingExtractor()
    parser = Parser(session, model, now=NOW)  # type: ignore[arg-type]

    first = await parser.parse(reply.id)
    second = await parser.parse(reply.id)

    assert model.calls == 1
    assert first.stored_price
    assert second.skipped == "уже разобран или решён человеком"
    assert second.tokens_spent == 0


@pytest.mark.usefixtures("secret")
async def test_parser_leaves_a_human_decision_alone(session: AsyncSession) -> None:
    reply = await _accepted(session)
    reply.reviewed_at = NOW
    reply.reviewed_by = "оператор@site.com"
    reply.price_white = Decimal("120")
    model = CountingExtractor()

    parsed = await Parser(session, model, now=NOW).parse(reply.id)  # type: ignore[arg-type]

    assert model.calls == 0
    assert parsed.skipped is not None
    assert reply.price_white == Decimal("120"), "цену человека разбор не трогает"


@pytest.mark.usefixtures("secret")
async def test_job_body_on_the_real_base_parses_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тело задачи очереди — на настоящей базе, платное подменено. Задача,
    пришедшая второй раз, модель не зовёт и говорит почему."""
    reply = await _accepted(session)
    await session.commit()
    model = CountingExtractor()
    monkeypatch.setattr(jobs, "ExtractClient", lambda: model)
    monkeypatch.setattr(jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )

    first = await jobs._parse_reply(reply.id)
    second = await jobs._parse_reply(reply.id)

    assert model.calls == 1
    assert first["stored_price"] is True
    assert first["skipped"] is None
    assert second["skipped"] == "уже разобран или решён человеком"
    assert second["tokens"] == 0
    await session.refresh(reply)
    assert reply.model_parse is not None
    donor = (await session.execute(select(DonorModel))).scalars().one()
    assert donor.last_price == Decimal("150.00")


def test_job_id_is_one_per_reply_and_valid_for_rq() -> None:
    """Номер задачи — от ответа и письма: повтор того же письма даёт тот же
    номер, а ответ с тем же номером после восстановления базы — другой."""
    first = parse_job_id(42, "<in-1@travelnotes.co.uk>")

    assert first == parse_job_id(42, "<in-1@travelnotes.co.uk>")
    assert first != parse_job_id(42, "<in-2@travelnotes.co.uk>")
    assert first != parse_job_id(43, "<in-1@travelnotes.co.uk>")
    assert JOB_ID_PATTERN.fullmatch(first)
    assert JOB_ID_PATTERN.fullmatch(parse_job_id(7))
