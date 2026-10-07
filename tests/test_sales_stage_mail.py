"""Этап продаж в общей почте, письма — срез 1.1b, часть «а»; после 4.6b.

Каждая ветка почты, где этап решает путь, обязана отказать продажам словами или
вести их своим путём, а не увести путём доноров. С 4.6b отправка и проход добивок
ведут продажи ответами модуля продаж, когда он подключён к мосту
(`tests/test_sales_stage_bridge.py`, `tests/test_sales_send.py`); здесь — мир, где продажи не подключены, и пути
доноров, которые продажи не ведут вовсе: их письма собирает модуль продаж.
Проверяется на базе, где путь доноров дал бы результат: домен письма продаж —
принятый донор с адресом, ящики есть у обоих этапов. Отказ здесь виден по тому,
чего в базе нет: письма не ушло, рассылки не заведено, срок добивки не погашен.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.cli.main import main
from backend.config import outreach as outreach_cfg
from backend.config import storage
from backend.features.core import stages
from backend.features.core.domain import (
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.core.stages import (
    SALES_ELSEWHERE,
    SALES_NOT_CONNECTED,
    SalesNotConnectedError,
)
from backend.features.letters import batch, draft, followups, probe, template
from backend.features.letters.answers import answer_reply
from backend.features.letters.building import BuildRequest, QueueBuilder, run_scope
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import Sending
from backend.features.letters.transport import NullTransport
from backend.features.runs.failures import is_permanent
from backend.workers import jobs
from httpx import AsyncClient
from sqlalchemy import Connection, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer, make_donor, make_sender
from tests.test_delivery_events import _keypair, _sign
from tests.test_mail_accounts import _ByStage
from tests.test_sales_model import ROOT, _migration

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LEAD = "lead.example.test"
MIGRATION = ROOT / "backend/migrations/versions/39e342cb2b21_stage_sales.py"


class NoRewrite:
    """Модель переписывания, которую звать нельзя: отказ продажам — раньше неё."""

    async def rewrite(self, *_: object) -> object:
        raise AssertionError("сборка продаж дошла до модели")


class NoJobs:
    """Очередь задач: отказ должен прийти раньше неё."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[object, ...]] = []

    def enqueue(self, *args: object, **_: object) -> object:
        self.enqueued.append(args)
        return type("Job", (), {"id": "job-1"})()


@dataclass(frozen=True, slots=True)
class World:
    """Письмо продаж домену, который заодно принятый донор с адресом."""

    letter: MessageModel
    thread: ThreadModel
    reply: ReplyModel


async def sender(session: AsyncSession, email: str, stage: Stage) -> SenderModel:
    """Ящик этапа, которым можно писать сегодня."""
    box = SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=stage,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
    )
    session.add(box)
    await session.flush()
    return box


async def sales_world(
    session: AsyncSession,
    *,
    status: MessageStatus = MessageStatus.QUEUED,
    due: datetime | None = None,
) -> World:
    """Рассылка продаж, её переписка, первое письмо и ответ человека на него."""
    domain = await make_donor(session, LEAD, email=f"ceo@{LEAD}")
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    box = await sender(session, "sales@mail-sales.example.test", Stage.SALES)
    campaign = CampaignModel(name="Продажи", stage=Stage.SALES, status="draft")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact_id)
    session.add(thread)
    await session.flush()
    sent = status is not MessageStatus.QUEUED
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact_id,
        step=0,
        status=status,
        subject="A question about your team",
        body="Hi,\n\nshort question.",
        idempotency_key=f"sales:{LEAD}:0",
        sender_id=box.id if sent else None,
        sent_at=NOW - timedelta(days=8) if sent else None,
        internet_message_id=f"<sales-1@{LEAD}>" if sent else None,
        next_action_at=due,
    )
    session.add(letter)
    await session.flush()
    reply = ReplyModel(
        thread_id=thread.id,
        message_id=letter.id,
        kind=ReplyKind.HUMAN,
        raw_body="Sounds interesting, tell me more.",
        from_email=f"ceo@{LEAD}",
        subject="Re: A question about your team",
        inbound_message_id=f"<in-1@{LEAD}>",
    )
    session.add(reply)
    await session.flush()
    return World(letter=letter, thread=thread, reply=reply)


# --- A1: значение типа ---------------------------------------------------------------


def _stage_values(connection: Connection) -> list[str]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его
    не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = _migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    return list(connection.execute(text("SELECT unnest(enum_range(NULL::stage))::text")).scalars())


async def test_a1_stage_type_has_sales_once_and_a_rerun_is_harmless(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_stage_values) == ["donors", "advertisers", "sales"]


# --- каждая точка отказа ----------------------------------------------------------------

Point = Callable[[AsyncSession, World], Awaitable[object]]


async def _pure(call: Callable[[], object]) -> object:
    return call()


#: Точки почты, где этап решает путь (таблица инвентаризации, часть «а»), — и что
#: они говорят продажам с 4.6b: пути доноров, которые продажи не ведут вовсе, —
#: «письма продаж собирает модуль продаж», допуск и отправка — «не подключены».
POINTS: dict[str, Point] = {
    "repository.candidates": lambda s, w: LetterRepository(s).candidates(Stage.SALES, limit=5),
    "repository.funnel": lambda s, w: LetterRepository(s).funnel(Stage.SALES),
    "building.run_scope": lambda s, w: run_scope(LetterRepository(s), (), stage=Stage.SALES),
    "sending._target": lambda s, w: Sending(s, NullTransport(), now=NOW).send(w.letter.id),
    "answers._context": lambda s, w: answer_reply(
        s,
        Sending(s, NullTransport(), now=NOW),
        thread_id=w.thread.id,
        reply_id=w.reply.id,
        body="Thanks, here is more.",
        author_id=None,
    ),
    "template.first_letter": lambda s, w: _pure(lambda: template.first_letter(Stage.SALES)),
    "template.for_stage": lambda s, w: _pure(lambda: template.for_stage(Stage.SALES)),
    "template.of_campaign": lambda s, w: _pure(lambda: template.of_campaign(Stage.SALES, None)),
    "template.followup": lambda s, w: _pure(lambda: template.followup(1, Stage.SALES)),
    "draft.default_draft": lambda s, w: _pure(lambda: draft.default_draft(Stage.SALES)),
    "draft.to_text": lambda s, w: _pure(lambda: draft.to_text("Hello", {}, Stage.SALES)),
    "probe.letter_for": lambda s, w: _pure(lambda: probe.letter_for(Stage.SALES)),
}


#: Точки, где продажи ещё не подключены (допуск и отправка); остальные — не их путь.
NOT_CONNECTED = frozenset({"building.run_scope", "sending._target", "answers._context"})


@pytest.mark.parametrize("point", sorted(POINTS))
async def test_every_mail_point_refuses_sales_in_words(
    session: AsyncSession, filled_legal: None, point: str
) -> None:
    world = await sales_world(session)
    words = SALES_NOT_CONNECTED if point in NOT_CONNECTED else SALES_ELSEWHERE

    with pytest.raises(SalesNotConnectedError, match=re.escape(words)):
        await POINTS[point](session, world)

    await session.refresh(world.letter)
    assert world.letter.status is MessageStatus.QUEUED


def test_refusal_is_permanent_for_the_job_queue() -> None:
    """Повтор задачи не подключит продажи: итог «не выполнена», а не три попытки."""
    assert is_permanent(SalesNotConnectedError("Очередь писем не собрана"))


# --- A2: письмо продаж в отправке --------------------------------------------------------


class TestSending:
    async def test_a2_letter_is_refused_and_stays_queued_untouched(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Путь доноров отправил бы его: домен — принятый донор, ящик есть. Отказ —
        до выбора транспорта этапа: учётку продаж письмо не трогает."""
        world = await sales_world(session)
        await make_sender(session, "anna@mail-donors.example.test")
        source = _ByStage(donors=NullTransport(), sales=NullTransport())

        with pytest.raises(SalesNotConnectedError) as refused:
            await Sending(session, source, now=NOW).send(world.letter.id)

        # Модуль продаж подключён к мосту: отказ называет, чего не хватает (`sales/connection.py`).
        assert str(refused.value).startswith(f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — ")
        assert source.asked == []
        await session.refresh(world.letter)
        assert world.letter.status is MessageStatus.QUEUED
        assert world.letter.sender_id is None
        assert world.letter.internet_message_id is None
        assert world.letter.next_action_at is None

    async def test_screen_gets_409_with_the_words(
        self,
        session: AsyncSession,
        filled_legal: None,
        client: AsyncClient,
        admin_token: str,
    ) -> None:
        world = await sales_world(session)
        await session.commit()

        response = await client.post(
            f"/api/letters/{world.letter.id}/send", headers=bearer(admin_token)
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail.startswith(f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — ")


# --- очередь этапа пачкой ---------------------------------------------------------------------


class TestSendQueue:
    async def test_the_batch_stops_on_a_sales_letter_in_words(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Не «связь с почтой оборвалась»: письмо продаж не уходило, и это известно."""
        world = await sales_world(session)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.SALES)

        assert report.stopped == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
        assert (report.sent, dict(report.refused), report.left) == (0, {}, 1)

    @pytest.mark.parametrize("letters", [0, 1])
    async def test_screen_gets_409_before_the_job_queue(
        self,
        session: AsyncSession,
        client: AsyncClient,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
        letters: int,
    ) -> None:
        """Почта этап не ведёт — 409 этими словами, а не «писем нет», и задачи нет,
        даже когда письмо продаж в очереди есть."""
        if letters:
            await sales_world(session)
            await session.commit()
        jobs_queue = NoJobs()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs_queue)

        response = await client.post(
            "/api/letters/send-queue", json={"stage": "sales"}, headers=bearer(admin_token)
        )

        assert response.status_code == 409
        assert response.json()["detail"] == f"Очередь писем не отправлена: {SALES_NOT_CONNECTED}"
        assert jobs_queue.enqueued == []


# --- учётка направления: события продаж принимает общий вебхук --------------------------


async def test_events_of_the_sales_account_are_taken_by_the_one_webhook(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Учётку этапа выбирает общий механизм (`config.outreach.mail_account`), ключи
    событий — по всем значениям `Stage`. Со значением `sales` вебхук принимает
    события учётки продаж, подписанные её ключом; своей копии выбора у продаж нет."""
    _, shared_public = _keypair()
    own, own_public = _keypair()
    monkeypatch.setattr(outreach_cfg, "EVENTS_PUBLIC_KEY", shared_public)
    monkeypatch.setenv("OUTREACH_SALES_EVENTS_PUBLIC_KEY", own_public)
    payload = b'[{"event":"delivered","message_id":"999999"}]'
    stamp = str(int(time.time()))

    response = await client.post(
        "/api/events/delivery",
        content=payload,
        headers={
            "X-Twilio-Email-Event-Webhook-Signature": _sign(own, payload, stamp),
            "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True


# --- A5: очередь продаж не из доноров -------------------------------------------------------


class TestQueue:
    async def test_a5_build_refuses_before_a_campaign_exists(self, session: AsyncSession) -> None:
        await make_donor(session, "donor-a.example.test", email="editor@donor-a.example.test")
        builder = QueueBuilder(session, NoRewrite())  # type: ignore[arg-type]

        with pytest.raises(SalesNotConnectedError, match="Очередь писем не собрана"):
            await builder.build(BuildRequest(campaign_name="Продажи", stage=Stage.SALES))

        campaigns = await session.scalar(
            select(func.count())
            .select_from(CampaignModel)
            .where(CampaignModel.stage == Stage.SALES)
        )
        letters = await session.scalar(select(func.count()).select_from(MessageModel))
        assert (campaigns, letters) == (0, 0)

    async def test_the_same_base_gives_donors_their_queue(self, session: AsyncSession) -> None:
        """Положительный контроль: отказ — этапу продаж, а не пустой базе."""
        await make_donor(session, "donor-a.example.test", email="editor@donor-a.example.test")

        picked = await LetterRepository(session).candidates(Stage.DONORS, limit=5)

        assert [candidate.host for candidate in picked] == ["donor-a.example.test"]

    async def test_screen_refuses_before_the_job_queue(
        self,
        session: AsyncSession,
        client: AsyncClient,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        jobs_queue = NoJobs()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs_queue)

        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Продажи", "stage": "sales"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == f"Очередь писем не собрана: {SALES_NOT_CONNECTED}"
        assert jobs_queue.enqueued == []

    async def test_queue_screen_of_sales_is_refused_in_words(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        response = await client.get("/api/letters?stage=sales", headers=bearer(admin_token))

        assert response.status_code == 409
        assert SALES_ELSEWHERE in response.json()["detail"]

    def test_console_says_it_with_its_own_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(storage, "REDIS_URL", "redis://localhost:1/0")
        monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

        code = main(["letters-build", "--campaign", "Продажи", "--stage", "sales"])

        assert code == 9
        assert (
            capsys.readouterr().err == f"Отказ: Очередь писем не собрана: {SALES_NOT_CONNECTED}\n"
        )

    def test_job_settles_it_as_an_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(jobs, "check_storage", lambda: None)
        monkeypatch.setattr(jobs, "setup_logging", lambda: None)

        result = jobs.build_letter_queue("Продажи", stage="sales")

        assert result == {
            "error": f"SalesNotConnectedError: Очередь писем не собрана: {SALES_NOT_CONNECTED}",
            "permanent": True,
        }


# --- A4: добивка продаж не теряется -----------------------------------------------------------


async def _donor_chain(session: AsyncSession, due: datetime) -> MessageModel:
    """Отправленное письмо донору со сроком добивки — соседняя цепочка прохода."""
    host = "donor-b.example.test"
    domain = await make_donor(session, host, email=f"editor@{host}")
    box = await make_sender(session, "anna@mail-donors.example.test")
    campaign = CampaignModel(name="Доноры", stage=Stage.DONORS, status="draft")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact_id,
        sender_id=box.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Hi there",
        sent_at=NOW - timedelta(days=8),
        internet_message_id=f"<donors-1@{host}>",
        next_action_at=due,
        idempotency_key=f"donors:{host}:0",
    )
    session.add(letter)
    await session.flush()
    return letter


class TestFollowup:
    async def test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on(
        self, session: AsyncSession, filled_legal: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Добивка продаж подошла раньше донорской: захват по сроку взял бы её
        первой, погасил срок и потерял на шаблоне."""
        due = NOW - timedelta(days=2)
        world = await sales_world(session, status=MessageStatus.SENT, due=due)
        donor = await _donor_chain(session, due=NOW - timedelta(days=1))

        with caplog.at_level(logging.WARNING, logger=followups.__name__):
            report = await followups.send_due(session, transport=NullTransport(), limit=5, now=NOW)

        assert (report.waiting, report.sent, report.postponed) == (1, 1, 0)
        assert "ждут этапа 1" in report.as_report
        await session.refresh(world.letter)
        await session.refresh(donor)
        assert world.letter.next_action_at == due
        assert donor.next_action_at is None  # её добивка ушла, срок следующей — у добивки
        assert SALES_NOT_CONNECTED in caplog.text
        followups_of_sales = await session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.thread_id == world.thread.id, MessageModel.step == 1)
        )
        assert followups_of_sales == 0

    def test_only_stages_with_templates_have_a_chain(self) -> None:
        assert template.CHAINED == (Stage.DONORS, Stage.ADVERTISERS)

    async def test_sending_refusal_gives_the_deadline_back(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Второй рубеж: проход счёл продажи подключёнными, а текст добивки собрать
        нельзя (отключили посреди прохода) — срок возвращается, добивка не теряется.
        С 4.6b текст продаж собирается до письма в базе: строки добивки нет."""
        monkeypatch.setattr(stages, "sales_connected", _connected)
        world = await sales_world(session, status=MessageStatus.SENT, due=NOW - timedelta(days=1))

        report = await followups.send_due(session, transport=NullTransport(), limit=5, now=NOW)

        assert (report.sent, report.postponed, report.waiting) == (0, 1, 0)
        await session.refresh(world.letter)
        assert world.letter.next_action_at == NOW + followups.POSTPONE
        materialized = await session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.thread_id == world.thread.id, MessageModel.step == 1)
        )
        assert materialized == 0


async def _connected(_session: AsyncSession) -> bool:
    return True


# --- ответ в переписке продаж -------------------------------------------------------------------


async def test_answer_in_a_sales_thread_leaves_no_letter_behind(
    session: AsyncSession, filled_legal: None
) -> None:
    """Отказ — до заведения письма: отказ отправки оставил бы ответ в очереди."""
    world = await sales_world(session, status=MessageStatus.SENT)

    with pytest.raises(SalesNotConnectedError, match=f"Ответ в переписке №{world.thread.id}"):
        await answer_reply(
            session,
            Sending(session, NullTransport(), now=NOW),
            thread_id=world.thread.id,
            reply_id=world.reply.id,
            body="Thanks, here is more.",
            author_id=None,
        )

    answers = await session.scalar(
        select(func.count()).select_from(MessageModel).where(MessageModel.step == ANSWER_STEP)
    )
    assert answers == 0


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[Any]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("admin@sales-stage.example.test", role=UserRole.ADMIN)
    return await sign_in("admin@sales-stage.example.test")
