"""Отправка очереди пачкой — «Отправить очередь · N» (слово Anthony 06.10.2026).

Проверяется то, на чём пачка держится: каждое письмо идёт тем же путём, что
одно, — лимит ящиков останавливает пачку, отказ по одному письму её не
останавливает и назван в итоге словами, обрыв связи с почтой останавливает
её, не отправляя дальше вслепую. Маршрут ставит задачу с доводами, которые
задача принимает, и не ставит её на пустую очередь.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import batch
from backend.features.letters.sending import SendError, SuppressedError
from backend.features.letters.transport import NullTransport, Outgoing, TransportError
from backend.shared.queue import SEND_QUEUE_JOB
from backend.workers import send_jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer, make_donor, make_sender
from tests.test_letters_queue import _build

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _queue(session: AsyncSession, count: int, *, cap: int = 20) -> list[MessageModel]:
    for n in range(count):
        await make_donor(session, f"donor{n}.example.test", email=f"info@donor{n}.example.test")
    await make_sender(session, "outreach1@mail.example.test", cap=cap)
    await _build(session)
    await session.commit()
    rows = await session.execute(select(MessageModel).order_by(MessageModel.id))
    return list(rows.scalars().all())


class Refusing:
    """Почта с предохранителем: «адрес не из своих» — на каждое письмо."""

    name = "refusing"
    real = True

    async def send(self, outgoing: Outgoing) -> str:
        raise TransportError(
            f"Адрес {outgoing.to} не в списке разрешённых получателей (OUTREACH_ALLOWED_RECIPIENTS)"
        )


class Silent:
    """Связь с почтой оборвалась: ушло письмо или нет — неизвестно."""

    name = "silent"
    real = True

    async def send(self, outgoing: Outgoing) -> str:
        raise ConnectionResetError("соединение сброшено")


class TestBatch:
    async def test_the_box_limit_stops_the_batch_and_the_rest_waits(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letters = await _queue(session, 3, cap=2)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert report.sent == 2
        assert report.left == 1
        assert report.stopped is not None
        assert "лимит" in report.stopped
        await session.refresh(letters[2])
        assert letters[2].status is MessageStatus.QUEUED

    async def test_the_rest_beyond_the_cap_is_counted_in_full(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Пачка берёт до потолка (`limit`, на бою `BATCH_MAX`), а «осталось в очереди» —
        вся очередь этапа: пять писем, потолок два — ушло два, осталось три, а не «два»."""
        await _queue(session, 5)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS, limit=2)

        assert (report.sent, report.stopped, report.left) == (2, None, 3)

    async def test_the_rest_is_what_the_next_batch_of_the_stage_takes(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Осталось» — первые письма этапа: оффер рекламодателю и добивка донора, которые
        тоже ждут в очереди, следующая пачка доноров не возьмёт, и в счёт они не идут."""
        letters = await _queue(session, 3)
        domain = DomainModel(host="offer.example.test")
        offer = CampaignModel(stage=Stage.ADVERTISERS, name="Оффер", status="running")
        session.add_all([domain, offer])
        await session.flush()
        last = letters[-1]
        session.add_all(
            [
                MessageModel(
                    campaign_id=offer.id,
                    domain_id=domain.id,
                    status=MessageStatus.QUEUED,
                    idempotency_key="made-up:offer:0",
                ),
                MessageModel(
                    campaign_id=last.campaign_id,
                    domain_id=last.domain_id,
                    step=1,
                    status=MessageStatus.QUEUED,
                    idempotency_key="made-up:followup:1",
                ),
            ]
        )
        await session.commit()

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS, limit=1)

        assert (report.sent, report.left) == (1, 2)

    async def test_one_refusal_does_not_stop_the_others(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Донора отклонили после сборки: его письмо не уходит, остальные — да."""
        await _queue(session, 3)
        donor = (
            (await session.execute(select(DonorModel).order_by(DonorModel.id))).scalars().first()
        )
        assert donor is not None
        donor.review = "rejected"
        await session.commit()

        report = await batch.send_queue(session, NullTransport(), stage=Stage.DONORS)

        assert report.sent == 2
        assert report.refused == {"донор отклонён после сборки": 1}
        assert report.stopped is None
        # Отказанное остаётся в очереди: «Не писать» по нему решает человек.
        assert report.left == 1

    async def test_the_safety_list_is_named_without_the_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отказ предохранителя называет адрес — итог не разваливается на строку
        на каждое письмо, а письма возвращаются в очередь."""
        await _queue(session, 2)

        report = await batch.send_queue(session, Refusing(), stage=Stage.DONORS)

        assert report.sent == 0
        assert report.refused == {"предохранитель: адрес не из своих": 2}
        assert report.left == 2

    async def test_a_broken_connection_stops_the_batch(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ушло ли письмо — неизвестно: дальше пачка не идёт вслепую."""
        letters = await _queue(session, 2)

        report = await batch.send_queue(session, Silent(), stage=Stage.DONORS)

        assert report.sent == 0
        assert report.stopped is not None
        assert f"№{letters[0].id}" in report.stopped
        await session.refresh(letters[1])
        assert letters[1].status is MessageStatus.QUEUED

    def test_refusals_are_named_by_kind(self) -> None:
        assert batch.why(SuppressedError("в стоп-листе")) == "стоп-лист"
        assert batch.why(SendError("платформа ответила 500")) == "почта отказала"

    def test_report_is_plain_data_for_the_job_result(self) -> None:
        report = batch.BatchReport(sent=3, stopped="лимит", left=1)
        report.refused["стоп-лист"] += 2

        assert report.as_dict() == {
            "sent": 3,
            "refused": {"стоп-лист": 2},
            "stopped": "лимит",
            "left": 1,
        }


class _Closable:
    async def dispose(self) -> None:
        return None


class TestTheJob:
    async def test_the_job_sends_the_stage_and_returns_the_report(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _queue(session, 2)
        monkeypatch.setattr(send_jobs, "create_async_engine", lambda _dsn: _Closable())
        monkeypatch.setattr(
            send_jobs,
            "async_sessionmaker",
            lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr(send_jobs, "Transports", NullTransport)

        result = await send_jobs._send(Stage.DONORS, None)

        assert result["sent"] == 2
        assert result["left"] == 0


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.calls.append((job, args))
        return type("Job", (), {"id": "job-пачка"})()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: fake)
    return fake


class TestTheRoute:
    async def test_queues_the_batch_with_arguments_the_job_accepts(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await _queue(session, 2)
        user = await make_user("отправитель@site.com", role=UserRole.ADMIN)
        token = await sign_in("отправитель@site.com")

        response = await client.post(
            "/api/letters/send-queue", json={"stage": "donors"}, headers=bearer(token)
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"job_id": "job-пачка", "queued": 2}
        path, args = queue.calls[0]
        assert path == SEND_QUEUE_JOB
        bound = inspect.signature(send_jobs.send_letter_queue).bind(*args)
        assert bound.arguments == {"stage": "donors", "author_id": user.id}

    async def test_empty_queue_is_refused_in_words(
        self,
        client: AsyncClient,
        queue: FakeQueue,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await make_user("отправитель@site.com", role=UserRole.ADMIN)
        token = await sign_in("отправитель@site.com")

        response = await client.post(
            "/api/letters/send-queue", json={"stage": "advertisers"}, headers=bearer(token)
        )

        assert response.status_code == 409
        assert "писем нет" in response.text
        assert queue.calls == []

    async def test_without_the_send_permission_nothing_is_queued(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        """Скомпрометированная учётка без права отправки не превращается в рассылку."""
        await _queue(session, 1)
        await make_user("оператор@site.com", role=UserRole.OPERATOR, permissions={"send": False})
        token = await sign_in("оператор@site.com")

        response = await client.post(
            "/api/letters/send-queue", json={"stage": "donors"}, headers=bearer(token)
        )

        assert response.status_code == 403
        assert queue.calls == []
