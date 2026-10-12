"""«Пишите другому» — до письма: имя нового лида из письма и его первое письмо сразу.

ТЗ: «дали контакт другого человека — пишем ему». Новый лид получает имя названного человека,
если собеседник его назвал (`contact_name` разбора, дословно из письма), иначе — без имени;
прошёл очистку — та же задача сборки очереди продаж собирает письмо только ему, сразу после
записи ответа, под тем же потолком модели. Проверка адресов не настроена — как раньше, «завести
лида руками». Всё — на настоящей базе; модель, DNS и очередь подставные; адреса выдуманные.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.contacts.mx import MailRoute
from backend.features.core.models.outreach import MessageModel
from backend.features.sales import cleaning, queue, queue_jobs
from backend.features.sales.models import LeadStatus, SalesThreadModel
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.features.sales.verifier import FixtureVerifier
from backend.workers import sales_jobs
from rq.exceptions import DuplicateJobError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_referral import COLLEAGUE, _answer, _leads, _origin, mail_is_accepted
from tests.test_sales_reply_kind import _job_body_on_test_base
from tests.test_sales_reply_routing import NOW, FakeClassifier, sales_letter, secret
from tests.test_sales_switch import sales_switched_on

__all__ = ["mail_is_accepted", "sales_switched_on", "secret"]  # оснастка «пишите другому»

NAMED = f"Это не ко мне. Пишите коллеге: Анна Петрова, {COLLEAGUE}"


def _referral(name: str | None) -> KindFound:
    return KindFound(
        SalesKind.REFERRAL, 0.9, quote="Это не ко мне", contact=COLLEAGUE, contact_name=name
    )


class Builds(list[tuple[int, int]]):
    """Сборки письма одному лиду: (гипотеза, лид)."""

    def __call__(self, hypothesis_id: int, lead_id: int) -> None:
        self.append((hypothesis_id, lead_id))


def _sales(session: AsyncSession, found: KindFound, builds: Builds) -> SalesReplies:
    return SalesReplies(
        session, FakeClassifier(found), verifier=FixtureVerifier, build_for=builds, now=NOW
    )


@pytest.mark.parametrize(("name", "kept"), [("Анна Петрова", "Анна Петрова"), (None, None)])
async def test_new_lead_gets_the_name_from_the_letter_or_none(
    session: AsyncSession, name: str | None, kept: str | None
) -> None:
    letter = await sales_letter(session)
    origin = await _origin(session, letter)
    reply = await _answer(session, letter, NAMED)
    builds = Builds()
    sales = _sales(session, _referral(name), builds)

    handled = await sales.handle(reply.id)
    await session.commit()
    await sales.write_to_referred(handled)

    [_, lead] = await _leads(session)
    assert (lead.email, lead.name, lead.status) == (COLLEAGUE, kept, LeadStatus.READY)
    assert handled.referred == (origin.hypothesis_id, lead.id)
    assert builds == [(origin.hypothesis_id, lead.id)], "письмо — сразу, только ему"


async def test_lead_that_did_not_pass_cleaning_gets_no_letter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, NAMED)

    async def no_mail(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.NONE

    monkeypatch.setattr(cleaning, "mail_route", no_mail)
    builds = Builds()
    sales = _sales(session, _referral("Анна Петрова"), builds)

    handled = await sales.handle(reply.id)
    await sales.write_to_referred(handled)

    [_, lead] = await _leads(session)
    assert lead.status is not LeadStatus.READY
    assert (handled.referred, builds) == (None, [])


async def test_without_the_verifier_the_lead_is_left_to_a_human_and_no_letter(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, NAMED)
    builds = Builds()
    sales = SalesReplies(session, FakeClassifier(_referral("Анна Петрова")), build_for=builds)

    handled = await sales.handle(reply.id)
    await sales.write_to_referred(handled)

    assert handled.reason is not None
    assert handled.reason.endswith("завести лида руками")
    assert (handled.referred, builds) == (None, [])


async def test_reply_job_builds_the_letter_only_after_the_answer_is_written(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, NAMED)
    await session.commit()
    _job_body_on_test_base(monkeypatch, session, FakeClassifier(_referral("Анна Петрова")))
    monkeypatch.setattr(sales_jobs, "build_verifier", lambda _http: FixtureVerifier())
    seen: list[str] = []

    def build_for(_hypothesis_id: int, lead_id: int) -> None:
        seen.append("build")
        assert lead_id > 0

    monkeypatch.setattr(queue_jobs, "build_for", build_for)
    commit = AsyncSession.commit

    async def logged(self: AsyncSession) -> None:
        await commit(self)
        seen.append("commit")

    monkeypatch.setattr(AsyncSession, "commit", logged)

    await sales_jobs.handle(reply.id)

    assert "build" in seen
    assert seen.index("build") > seen.index("commit"), "задача сборки видит записанного лида"


async def test_queue_down_leaves_the_ready_lead_to_the_hypothesis_build(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    letter = await sales_letter(session)
    await _origin(session, letter)
    reply = await _answer(session, letter, NAMED)

    def refused(_hypothesis_id: int, _lead_id: int) -> None:
        raise ConnectionError("очередь не отвечает")

    sales = SalesReplies(
        session, FakeClassifier(_referral(None)), verifier=FixtureVerifier, build_for=refused
    )
    handled = await sales.handle(reply.id)

    await sales.write_to_referred(handled)

    assert "письмо новому лиду не поставлено — соберёт сборка гипотезы" in caplog.text


# --- сборка только этому лиду -------------------------------------------------------------


async def test_build_for_one_lead_writes_only_to_that_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await w.world(session, monkeypatch)
    jane = await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    olga = await w.lead(session, world.hypothesis_id, "olga@acme.example.test")

    report = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5, only=(olga.id,)
    )

    assert report.prepared == 1
    written = list(await session.scalars(select(SalesThreadModel.lead_id)))
    assert written == [olga.id], "сборка всех лидов вместо одного"
    assert jane.id not in written
    [first] = await session.scalars(select(MessageModel).where(MessageModel.step == 0))
    assert first.status.value == "queued", "первое письмо цепочки — в очередь, как у всех"


class Queue:
    def __init__(self, *, duplicate: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.duplicate = duplicate

    def enqueue(self, path: str, *args: Any, **options: Any) -> None:
        if self.duplicate:
            raise DuplicateJobError(options["job_id"])
        self.jobs.append((path, args, options))


def test_build_for_puts_the_same_build_job_for_one_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    placed = Queue()
    monkeypatch.setattr(queue_jobs, "sales_queue", lambda: placed)

    queue_jobs.build_for(5, 41)

    [(path, args, options)] = placed.jobs
    assert (path, args) == (queue_jobs.QUEUE_JOB, (5, 1, [41]))
    assert (options["job_id"], options["unique"]) == ("sales-queue-lead-41", True)


def test_build_for_an_already_queued_lead_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue_jobs, "sales_queue", lambda: Queue(duplicate=True))

    queue_jobs.build_for(5, 41)  # не исключение: письмо уже собирается


async def test_job_with_leads_builds_only_them(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def build(_session: AsyncSession, _rewriter: object, **kwargs: Any) -> queue.QueueReport:
        seen.append(kwargs)
        return queue.QueueReport(campaign_id=3, prepared=1)

    monkeypatch.setattr(queue, "build", build)
    monkeypatch.setattr(queue_jobs, "create_async_engine", lambda _dsn: _Engine())
    monkeypatch.setattr(queue_jobs, "async_sessionmaker", lambda _engine, **_kw: _Factory())

    await queue_jobs.run_build(5, 1, [41])

    assert seen == [{"hypothesis_id": 5, "limit": 1, "only": (41,)}]


class _Engine:
    async def dispose(self) -> None:
        return None


class _Factory:
    def __call__(self) -> _Factory:
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> None:
        return None
