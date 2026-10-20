"""Перепроверка адреса, у которого Hunter «ещё проверяет» (202), — сама, по сроку, в окне времени.

Разбор прогона сбора: после переспросов прохода лид оставался «новым» со словами «повторим
следующей очисткой», и следующей очистки никто не запускал. Здесь: срок и окно у лида, слова с
датой и временем, задача очереди на срок (`enqueue_at`, своего планировщика нет), тело задачи на
настоящей базе, предел — временем от первого «ещё проверяет» (нажатие раньше срока его не
сокращает), временный отказ посреди окна срок не снимает, вердикт — в месячный потолок, 202 —
нет; продажи выключены — задача ничего не делает; расход — в отчёт прогона и запись в журнале.

Hunter — `httpx.MockTransport` или выдуманный проверяльщик (`still-checking@` — «ещё проверяет»),
очередь — подделка с правилами rq 2.12. Адреса и ниши разные, домены выдуманы, IDN — в punycode.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config.startup_checks import ConfigError
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import ProviderError
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.ops import job_outcome
from backend.features.sales import cleaning, deferred, hunter_codes, verify_again, verify_again_jobs
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.segment_estimate import SALES_OFF
from backend.features.sales.segment_models import Lane, SalesSegmentRunModel, Stage
from backend.features.sales.verifier import OPERATION, FixtureVerifier, HunterVerifier, Verdict
from backend.features.sales.verify_cap import MonthCapped
from backend.shared.net import retry
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import DuplicateJobError
from rq.job import JobStatus
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_sales_clean_api import _Job, _Redis
from tests.test_sales_cleaning import NOW, World
from tests.test_sales_segment_rejudge import site
from tests.test_sales_segment_run import make_run

#: Умолчания настроек: перепроверка через двадцать минут, окно — три такие перепроверки.
TERM = timedelta(minutes=20)
WINDOW = 3 * TERM
#: Лиды разных ниш, рынков и зон, с доменами IDN в punycode: пекарня (.de), клиника (.com.br),
#: чайная (IDN в .jp), книжная лавка (IDN в .рф), магазин книг (IDN в зоне IDN, .امارات).
PENDING = "still-checking.bakery@xn--bckerei-beispiel-vnb.de"
CHECKING = "still-checking-office@clinica-sample.com.br"
FINE = "kenji@xn----ifuwe8csby996gnwb.jp"
OTHER = "salma@xn----zmcbedj7noabf.xn--mgbaam7a8h"
BOOKS = "zakaz@xn-----6kcbalxbrhngkudc6bsd10a.xn--p1ai"


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Почту домены принимают; паузы переспросов Hunter не спят; потолок месяца — с запасом."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr(retry, "_sleep", pause)
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")


class Later:
    """Очередь долгих задач продаж с правилами rq 2.12, что нужны отложенной постановке: задача
    по номеру своей очереди, `enqueue_at` — «на срок», `enqueue` с `unique=True` — занятый номер
    отказывает, пока след задачи не удалён."""

    serializer = None

    def __init__(self) -> None:
        self.connection = _Redis()
        self.known: dict[str, _Job] = {}
        self.planned: list[tuple[datetime, str, tuple[object, ...], str]] = []
        self.enqueued: list[tuple[str, tuple[object, ...], str]] = []

    def fetch_job(self, number: str) -> _Job | None:
        found = self.known.get(number)
        return None if found is None or found.deleted else found

    def enqueue_at(self, when: datetime, path: str, *args: object, **kwargs: Any) -> _Job:
        number = str(kwargs["job_id"])
        assert "retry" in kwargs  # повтор после сбоя — как у прочих задач продаж
        self.planned.append((when, path, args, number))
        self.known[number] = _Job(number, path, self.connection)
        self.known[number].status = JobStatus.SCHEDULED
        return self.known[number]

    def enqueue(self, path: str, *args: object, **kwargs: Any) -> _Job:
        number = str(kwargs["job_id"])
        if kwargs.get("unique") and self.fetch_job(number) is not None:
            raise DuplicateJobError(number)
        self.enqueued.append((path, args, number))
        self.known[number] = _Job(number, path, self.connection)
        return self.known[number]


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> Later:
    found = Later()
    monkeypatch.setattr(deferred, "sales_bulk_queue", lambda: found)
    return found


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="пекарни DE")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


async def _lead(session: AsyncSession, lead_id: int) -> SalesLeadModel:
    found = await session.get(SalesLeadModel, lead_id, populate_existing=True)
    assert found is not None
    return found


def _words(at: datetime, until: datetime, why: str = "Hunter ещё проверяет адрес") -> str:
    return (
        f"проверка не выполнена: {why} — перепроверка сама около {at:%d.%m %H:%M} UTC, "
        f"ждём до {until:%d.%m %H:%M} UTC"
    )


class Refusing:
    """Проверяльщик, у которого сервис временно не отвечает: 5xx словами общего разбора."""

    name = "hunter"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        raise ProviderError("провайдер не ответил (HTTP 503)")


# --- исход «ещё проверяет»: срок, окно, слова ------------------------------------------------------


async def test_still_checking_keeps_the_lead_new_with_the_term_the_window_and_words(
    world: World, later: Later
) -> None:
    pending = await world.lead(PENDING)
    fine = await world.lead(FINE)

    report = await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    waiting = await _lead(world.session, pending.id)
    assert (waiting.status, waiting.verify_since, waiting.verify_again_at) == (
        LeadStatus.NEW,
        NOW,
        NOW + TERM,
    )
    # Дата и время — по UTC, со днём: «около 09:50» завтрашним утром читалось бы прошедшим.
    assert waiting.cleaning_note == (
        "проверка не выполнена: Hunter ещё проверяет адрес — перепроверка сама около "
        "04.10 09:50 UTC, ждём до 04.10 10:30 UTC"
    )
    assert waiting.verification_status is None
    ready = await _lead(world.session, fine.id)
    assert (ready.status, ready.verify_since, ready.verify_again_at) == (
        LeadStatus.READY,
        None,
        None,
    )
    assert (report.still_checking, report.again_at, report.unverified) == (1, NOW + TERM, 0)
    # Задача на срок — очередью долгих задач продаж, номер — от минуты срока.
    assert later.planned == [(NOW + TERM, verify_again.JOB, (), "sales-verify-again-20261004T0950")]


async def test_a_previous_verdict_stays_while_hunter_is_still_checking(
    world: World, later: Later
) -> None:
    """Первый `unknown` неделю назад: перепроверка в свой день — «ещё проверяет»; вердикт цел, и
    следующий `unknown` будет вторым — отказом, а не новым первым."""
    lead = await world.lead(CHECKING)
    earlier = NOW - timedelta(days=8)
    lead.verification_status, lead.verification_score, lead.verified_at = (
        "fixture:unknown",
        48,
        earlier,
    )
    await world.session.flush()

    await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    found = await _lead(world.session, lead.id)
    assert (found.verification_status, found.verified_at) == ("fixture:unknown", earlier)
    assert (found.verify_since, found.verify_again_at) == (NOW, NOW + TERM)
    assert len(later.planned) == 1


async def test_after_the_window_the_lead_is_refused_in_words(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Окно — `SALES_VERIFY_AGAIN_TRIES` сроков по `SALES_VERIFY_AGAIN_MIN` от первого «ещё
    проверяет»: последний срок — конец окна, а «ещё проверяет» после него — отказ «не подтверждён»
    словами; срок и окно сняты, задачи на срок больше нет."""
    monkeypatch.setattr("backend.config.sales.VERIFY_AGAIN_TRIES", 2)
    monkeypatch.setattr("backend.config.sales.VERIFY_AGAIN_MIN", 45)
    lead = await world.lead(PENDING)
    until = NOW + timedelta(minutes=90)

    await cleaning.clean(world.session, FixtureVerifier(), now=NOW)
    await cleaning.clean(world.session, FixtureVerifier(), now=NOW + timedelta(minutes=45))
    second = await _lead(world.session, lead.id)
    assert (second.verify_since, second.verify_again_at) == (NOW, until)
    await cleaning.clean(world.session, FixtureVerifier(), now=until)

    found = await _lead(world.session, lead.id)
    assert (found.status, found.rejection_reason) == (
        LeadStatus.REJECTED,
        RejectionReason.UNCONFIRMED.value,
    )
    assert found.cleaning_note == (
        "адрес не проверить: Hunter не закончил проверку и за 90 мин — перепроверяли каждые 45 мин"
    )
    assert (found.verify_since, found.verify_again_at) == (None, None)
    assert len(later.planned) == 2  # третий проход отказал — нового срока нет


async def test_pressing_early_does_not_shorten_the_window(world: World, later: Later) -> None:
    """Ревью 20.10: счёт заходов сжигался нажатиями — три «Очистить» подряд, и лид отклонён.
    Теперь предел — временем: шесть заходов за окно (каждые десять минут, раньше срока) его не
    сокращают, лид ждёт дальше; последний срок — конец окна."""
    lead = await world.lead(PENDING)

    for minute in range(0, 60, 10):
        await cleaning.clean(world.session, FixtureVerifier(), now=NOW + timedelta(minutes=minute))

    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_since, found.verify_again_at) == (
        LeadStatus.NEW,
        NOW,
        NOW + WINDOW,
    )
    assert found.cleaning_note == _words(NOW + WINDOW, NOW + WINDOW)


async def test_any_other_outcome_takes_the_term_and_the_window_away(
    world: World, later: Later
) -> None:
    lead = await world.lead(FINE)
    lead.verify_since, lead.verify_again_at = NOW - TERM, NOW - timedelta(minutes=1)
    await world.session.flush()

    await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_since, found.verify_again_at) == (
        LeadStatus.READY,
        None,
        None,
    )
    assert later.planned == []


async def test_a_real_address_is_not_taken_for_still_checking() -> None:
    """Выдуманный проверяльщик откладывает только длинный маркер: настоящий адрес вроде
    `pending.orders@` при `fixture` проходит, как и проходил (ревью 20.10)."""
    fixture = FixtureVerifier()

    verdicts = [
        (await fixture.verify(email)).status
        for email in ("pending.orders@sklep-sample.pl", "checking-in@hotel-sample.gr")
    ]

    assert verdicts == ["valid", "valid"]
    with pytest.raises(hunter_codes.StillCheckingError):
        await fixture.verify("still-checking@sklep-sample.pl")


# --- временный отказ посреди окна -------------------------------------------------------------------


async def test_a_passing_refusal_in_the_window_keeps_the_term(world: World, later: Later) -> None:
    """Ревью 20.10: 5xx, сеть, частота посреди окна снимали срок, и лид висел «новым» без
    перепроверки. Теперь — как «ещё проверяет»: срок следующий, окно прежнее, слова — отказа."""
    lead = await world.lead(OTHER)
    since = NOW - timedelta(minutes=30)
    lead.verify_since, lead.verify_again_at = since, NOW - timedelta(minutes=1)
    await world.session.commit()
    hunter = Refusing()

    report = await verify_again_jobs.again_pass(world.session, hunter, now=NOW)

    assert report is not None
    assert hunter.asked == [OTHER]
    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_since, found.verify_again_at) == (
        LeadStatus.NEW,
        since,
        NOW + TERM,
    )
    assert found.cleaning_note == _words(
        NOW + TERM, since + WINDOW, "провайдер не ответил (HTTP 503)"
    )
    assert [when for when, *_ in later.planned] == [NOW + TERM]


async def test_a_passing_refusal_after_the_window_leaves_the_lead_in_words(
    world: World, later: Later
) -> None:
    lead = await world.lead(OTHER)
    lead.verify_since, lead.verify_again_at = NOW - WINDOW, NOW - timedelta(minutes=1)
    await world.session.commit()

    await verify_again_jobs.again_pass(world.session, Refusing(), now=NOW)

    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_since, found.verify_again_at) == (LeadStatus.NEW, None, None)
    assert found.cleaning_note == "проверка не выполнена: провайдер не ответил (HTTP 503)"
    assert later.planned == []


# --- задача на срок: тело на базе ------------------------------------------------------------------


async def test_the_recheck_takes_only_the_leads_whose_term_came(world: World, later: Later) -> None:
    """Срок настал (и в пределах минуты) — перепроверка; ещё не настал, отклонён или без срока —
    не трогает. Перепроверенный «ещё проверяет» — следующий срок, окно прежнее."""
    due = await world.lead(PENDING)
    soon = await world.lead(FINE)
    future = await world.lead(OTHER)
    gone = await world.lead("still-checking@gone-sample.co.uk", status=LeadStatus.REJECTED)
    plain = await world.lead(BOOKS)
    since = NOW - timedelta(minutes=25)
    due.verify_since, due.verify_again_at = since, NOW - timedelta(minutes=5)
    soon.verify_since, soon.verify_again_at = since, NOW + timedelta(seconds=40)
    future.verify_since, future.verify_again_at = since, NOW + timedelta(minutes=9)
    gone.verify_since, gone.verify_again_at = since, NOW - timedelta(minutes=5)
    await world.session.commit()

    report = await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW)

    assert report is not None
    assert report.checked == 2
    rows = {
        lead.id: (lead.status, lead.verify_since, lead.verify_again_at)
        for lead in (await world.session.scalars(select(SalesLeadModel))).all()
    }
    assert rows[due.id] == (LeadStatus.NEW, since, NOW + TERM)
    assert rows[soon.id] == (LeadStatus.READY, None, None)
    assert rows[future.id] == (LeadStatus.NEW, since, NOW + timedelta(minutes=9))
    assert rows[gone.id][0] is LeadStatus.REJECTED
    assert rows[plain.id] == (LeadStatus.NEW, None, None)


def test_the_recheck_takes_its_leads_under_a_lock_and_skips_taken_ones() -> None:
    """Ревью 20.10: две задачи на соседние минуты и «Повторить сейчас» у очистки проверили бы один
    адрес дважды. Выборка задачи — `FOR UPDATE … SKIP LOCKED` по строкам лидов."""
    sql = str(verify_again_jobs.due_rows(NOW).compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]

    assert sql.endswith("FOR UPDATE OF sales_leads SKIP LOCKED")


async def _run_with_lead(
    world: World, *, going: bool
) -> tuple[SalesSegmentRunModel, SalesLeadModel]:
    """Прогон сбора с лидом «ещё проверяет», чей срок настал; `going` — проход прогона идёт."""
    run = await make_run(world.session, world.hypothesis)
    run.stage, run.finished_at = Stage.DONE, None if going else NOW - timedelta(hours=1)
    lead = await world.lead(CHECKING)
    lead.verify_since, lead.verify_again_at = NOW - TERM, NOW - timedelta(minutes=2)
    host = CHECKING.rpartition("@")[2]
    candidate = await site(world.session, run, host, lane=Lane.ACCEPT)
    candidate.lead_id = lead.id
    await world.session.commit()
    return run, lead


async def test_the_recheck_leaves_the_leads_of_a_going_run_to_the_run(
    world: World, later: Later
) -> None:
    """Проход прогона идёт — его лидов задача не берёт (запись в отчёт прогона затёрла бы одна
    другую), и срок не ставит: его поставит конец прохода."""
    _, lead = await _run_with_lead(world, going=True)

    assert await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW) is None
    assert await verify_again.next_at(world.session, NOW) is None
    assert (await _lead(world.session, lead.id)).verify_again_at == NOW - timedelta(minutes=2)


class Paying:
    """Живой проверяльщик: каждый вызов — платный вердикт «годен»; спрошенные — в список."""

    name = "hunter"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        return Verdict("valid", 91, units=1)


async def _units(session: AsyncSession) -> int:
    found = await session.scalar(
        select(func.coalesce(func.sum(UsageRecordModel.units), 0)).where(
            UsageRecordModel.operation == OPERATION
        )
    )
    return int(found or 0)


async def _job_session(world: World) -> AsyncSession:
    """Сессия задачи на соединении теста: её коммит сохраняет, откат — снимает несохранённое, как
    у настоящей задачи (точками сохранения внутри внешней транзакции теста)."""
    connection = await world.session.connection()
    return AsyncSession(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


async def test_a_failure_after_paid_verdicts_keeps_them_and_asks_nobody_again(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревью 20.10, второй круг: задача коммитила один раз в конце, и сбой после вердиктов
    откатывал их и строки расхода — лид оставался со старым сроком, а повтор задачи платил за него
    снова. Теперь вердикты и расход — коммитом партии: после сбоя и отката задачи лид решён, и
    Hunter о нём больше не спрашивают."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "live")
    _, lead = await _run_with_lead(world, going=False)
    lead_id = lead.id
    hunter = Paying()
    real = cleaning.clean_leads

    async def then_fail(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise ConnectionError("база мигнула после вердиктов")

    job = await _job_session(world)
    monkeypatch.setattr(cleaning, "clean_leads", then_fail)
    with pytest.raises(ConnectionError):
        await verify_again_jobs.again_pass(job, hunter, now=NOW)
    await job.rollback()  # задача упала: несохранённое — снято
    monkeypatch.setattr(cleaning, "clean_leads", real)

    again = await verify_again_jobs.again_pass(job, hunter, now=NOW)
    await job.close()

    found = await _lead(world.session, lead_id)
    assert (found.status, found.verification_status, found.verify_again_at) == (
        LeadStatus.READY,
        "hunter:valid",
        None,
    )
    assert (await _units(world.session), hunter.asked, again) == (1, [CHECKING], None)


async def test_a_failure_of_the_run_report_keeps_the_verdicts(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отчёт прогона и запись в журнале — своей транзакцией после партии: их сбой — строка журнала
    сервиса, а вердикты и расход остаются."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "live")
    run, lead = await _run_with_lead(world, going=False)
    run_id, lead_id = run.id, lead.id

    def broken(*_args: object) -> dict[str, Any]:
        raise ValueError("отчёт прогона не разобран")

    monkeypatch.setattr(verify_again_jobs, "leads_report", broken)

    report = await verify_again_jobs.again_pass(world.session, Paying(), now=NOW)

    assert report is not None
    found = await _lead(world.session, lead_id)
    assert (found.status, await _units(world.session)) == (LeadStatus.READY, 1)
    refreshed = await world.session.get(SalesSegmentRunModel, run_id, populate_existing=True)
    assert refreshed is not None
    assert "leads" not in refreshed.report
    assert any("вердикты сохранены" in record.getMessage() for record in caplog.records)


async def test_the_recheck_takes_the_leads_of_a_run_whose_pass_went_quiet(
    world: World, later: Later
) -> None:
    """Ревью 20.10, второй круг: проход «идёт», пока пуст его конец, — а у потерянной задачи и
    убитого воркера он пуст навсегда, и перепроверка лидов прогона вставала. Теперь проход идёт,
    только пока его строка отмечается (`verify_again.QUIET`)."""
    run, lead = await _run_with_lead(world, going=True)
    run.updated_at = NOW - verify_again.QUIET - timedelta(minutes=1)
    await world.session.commit()

    assert await verify_again.next_at(world.session, NOW) == NOW - timedelta(minutes=2)
    report = await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW)

    assert report is not None
    assert (await _lead(world.session, lead.id)).verify_again_at == NOW + TERM


async def test_the_window_starts_at_the_answer_not_at_the_pass(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревью 20.10, второй круг: окно и срок считались от начала прохода, и в долгом проходе лид,
    чей 202 пришёл через минуты, получал срок уже в прошлом. Теперь — от ответа (с шагом минута)."""
    ticks = iter([1000.0])
    monkeypatch.setattr(verify_again, "_clock", lambda: next(ticks, 1000.0 + 125))
    lead = await world.lead(PENDING)

    await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    found = await _lead(world.session, lead.id)
    answered = NOW + timedelta(minutes=2)
    assert (found.verify_since, found.verify_again_at) == (answered, answered + TERM)


async def test_the_recheck_writes_its_spend_into_the_run_and_the_journal(
    world: World, later: Later
) -> None:
    """Ревью 20.10: проверки задачи мимо отчёта прогона — панель расхода занижала Hunter. Теперь
    проверки — в `report.leads.cleaning` прогона, а в журнале — «автоматически, по сроку»."""
    run, lead = await _run_with_lead(world, going=False)
    run.report = {"leads": {"created": 4, "cleaning": {"checked": 4, "paid_units": 3}}}
    await world.session.commit()

    await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW)

    refreshed = await world.session.get(SalesSegmentRunModel, run.id, populate_existing=True)
    assert refreshed is not None
    cleaning_report = refreshed.report["leads"]["cleaning"]
    assert (cleaning_report["checked"], refreshed.report["leads"]["created"]) == (5, 4)
    assert (await _lead(world.session, lead.id)).status is LeadStatus.NEW  # всё ещё проверяет
    journal = await world.session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert (journal.user_id, journal.details) == (
        None,
        {"этап": "sales", "действие": verify_again_jobs.AUTO, "прогон": run.id, "лидов": 1},
    )


async def test_the_recheck_with_nothing_due_is_empty(world: World) -> None:
    await world.lead(FINE)
    await world.session.commit()

    assert await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW) is None


async def test_the_next_term_is_the_earliest_left_in_the_base(world: World, later: Later) -> None:
    """Следующая задача — на самый ранний срок в базе, не ближе минуты от «сейчас» (после сбоя —
    не ближе срока); минута, у которой задача уже есть, второй не получает."""
    first = await world.lead(PENDING)
    second = await world.lead(OTHER)
    first.verify_since, first.verify_again_at = NOW, NOW + timedelta(minutes=7)
    second.verify_since, second.verify_again_at = NOW, NOW - timedelta(minutes=3)
    await world.session.flush()

    planned = await verify_again.plan(world.session, now=NOW)
    again = await verify_again.plan(world.session, now=NOW)
    after_failure = await verify_again.plan(world.session, now=NOW, soonest=TERM)

    assert planned == "sales-verify-again-20261004T0931"
    assert again is None
    assert after_failure == "sales-verify-again-20261004T0950"
    assert [(when, number) for when, _, _, number in later.planned] == [
        (NOW + deferred.SOONEST, "sales-verify-again-20261004T0931"),
        (NOW + TERM, "sales-verify-again-20261004T0950"),
    ]


async def test_no_terms_no_job(world: World, later: Later) -> None:
    await world.lead(FINE)
    await world.session.flush()

    assert await verify_again.plan(world.session, now=NOW) is None
    assert later.planned == []


async def test_a_silent_queue_does_not_fail_the_cleaning(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Очередь не ответила — проход очистки идёт: срок лежит в базе, а строка журнала говорит,
    что задачу поставит следующий проход."""

    def down() -> object:
        raise RedisConnectionError("очередь недоступна")

    monkeypatch.setattr(deferred, "sales_bulk_queue", down)
    lead = await world.lead(PENDING)

    report = await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    assert report.still_checking == 1
    assert (await _lead(world.session, lead.id)).verify_again_at == NOW + TERM
    assert any("отложенная задача не поставлена" in r.getMessage() for r in caplog.records)


# --- деньги: 202 бесплатен, вердикт — в месячный потолок -------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]
STILL = httpx.Response(202, json={"data": {"status": "pending"}})
VALID = httpx.Response(200, json={"data": {"status": "valid", "score": 88}})


def _script(*answers: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    """Ответы Hunter по очереди — последний повторяется; запросы — в список."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers[min(len(seen), len(answers)) - 1]

    return handler, seen


async def test_still_checking_costs_no_place_under_the_month_cap_and_the_verdict_does() -> None:
    handler, seen = _script(STILL)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capped = MonthCapped(HunterVerifier(http, api_key="k-test"), cap=5, spent=3)
        with pytest.raises(hunter_codes.StillCheckingError):
            await capped.verify(PENDING)

    assert (capped.used, len(seen)) == (3, 3)  # три запроса (с переспросами) — место вернулось

    handler, _ = _script(VALID)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capped = MonthCapped(HunterVerifier(http, api_key="k-test"), cap=5, spent=3)
        await capped.verify(PENDING)
    assert capped.used == 4


async def test_the_recheck_asks_hunter_under_the_month_cap(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок месяца выбран — перепроверка не спрашивает Hunter и встаёт его словами; посреди
    окна срок остаётся (временный отказ — как «ещё проверяет»), окно его ограничит."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "0")
    lead = await world.lead(FINE)
    lead.verify_since, lead.verify_again_at = NOW - TERM, NOW - timedelta(minutes=2)
    await world.session.commit()
    handler, seen = _script(VALID)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        hunter = HunterVerifier(http, api_key="k-test")
        report = await verify_again_jobs.again_pass(world.session, hunter, now=NOW)

    assert report is not None
    assert seen == []
    assert (
        report.stopped
        == "потолок проверки адресов на месяц выбран: 0 из 0 — следующий месяц продолжит"
    )
    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_again_at) == (LeadStatus.NEW, NOW + TERM)


# --- задача очереди: продажи выключены, отказы как у очистки, срок после сбоя ----------------------


async def test_the_recheck_with_sales_off_asks_nobody(
    later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревью 20.10: выключенные продажи не останавливали задачу — Hunter спрашивался и новая
    задача ставилась. Теперь — пропуск словами: ни проверки, ни задачи."""
    built: list[object] = []
    monkeypatch.setattr("backend.config.sales.ENABLED", False)
    monkeypatch.setattr(verify_again_jobs, "build_verifier", built.append)

    assert await verify_again_jobs.run_again() == {"skipped": SALES_OFF}
    assert (built, later.planned) == ([], [])


async def test_a_failed_recheck_plans_no_next_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ревью 20.10, второй круг: упавшая перепроверка ставила свою новую задачу, и вместе с
    повторами очереди выходил круг «упала — поставила — упала», платный на каждой попытке. Теперь
    новой задачи упавшая не ставит: повтор — за очередью, сроки ждут в базе."""
    planned: list[datetime] = []

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("база мигнула")

    async def plan(_session: AsyncSession, *, now: datetime, **_kw: object) -> None:
        planned.append(now)

    monkeypatch.setattr("backend.config.sales.ENABLED", True)
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    monkeypatch.setattr(verify_again_jobs, "again_pass", broken)
    monkeypatch.setattr(verify_again, "plan", plan)

    with pytest.raises(ConnectionError):
        await verify_again_jobs.run_again()

    assert planned == []


def test_the_job_settles_a_misconfigured_verifier_as_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused() -> dict[str, Any]:
        raise ConfigError("SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст")

    monkeypatch.setattr(verify_again_jobs, "run_again", refused)
    monkeypatch.setattr(verify_again_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(verify_again_jobs, "check_storage", lambda: None)

    assert verify_again_jobs.recheck() == {
        "error": "проверка адресов не настроена — настраивает администратор",
        "permanent": True,
    }


def test_the_job_lets_a_passing_failure_go_to_the_queue_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken() -> dict[str, Any]:
        raise ConnectionError("база не ответила")

    monkeypatch.setattr(verify_again_jobs, "run_again", broken)
    monkeypatch.setattr(verify_again_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(verify_again_jobs, "check_storage", lambda: None)

    with pytest.raises(ConnectionError):
        verify_again_jobs.recheck()


def test_the_job_path_is_importable_by_the_worker() -> None:
    module, _, name = verify_again.JOB.rpartition(".")
    assert (module, name) == ("backend.features.sales.verify_again_jobs", "recheck")
    assert callable(getattr(verify_again_jobs, name))


def test_the_job_is_named_in_words_on_the_screen() -> None:
    """Строка задачи на экране говорит, что это за задача, а не путь функции; задача — продаж."""
    assert job_outcome.KINDS[verify_again.JOB] == "перепроверка адресов продаж"
    assert job_outcome.sales_job("x", verify_again.JOB, None)
