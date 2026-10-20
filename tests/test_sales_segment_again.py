"""Повтор поиска адреса сайтам прогона, которые не ответили, — сам, по сроку; «ждут адреса» и
кнопка — условием прохода; «Повторить сейчас», строки ожидания и предложения повтора в отчёте.

Разбор прогона сбора: проход адресов ставили только запуск и «Продолжить», а «ждут адреса» брал
лишь не искавшиеся — сайт с наступившим сроком повтора никто не искал и кнопки он не получал.
Здесь: срок — одним правилом в Python и в SQL, не раньше `SALES_SEGMENT_AGAIN_MIN` и для прохода,
и для числа; срок после любого прохода — задачей очереди (`enqueue_at`), и после сбоя тоже;
задача на срок ставит проход под номером прогона с журналом «автоматически, по сроку», а пропуск
(проход или пересуд идёт) — новым сроком; проход по сроку ищет только «не ответил» с наступившим
сроком и заводит лиды только из найденного им (и после повтора задачи); стадию, причину и ход
прохода повтор не трогает; кнопка берёт только наступившие сроки, а «не спросили» — при платной
ступени; платный поиск повтора — в последний проход и в пределах сметы (ревью 20.10). Что
называет отчёт и что возьмёт кнопка, маршрут «Повторить сейчас» —
`test_sales_segment_again_report.py`.

Сайты — `httpx.MockTransport`: главная с адресом или обрыв соединения; DNS — «почта есть»;
проверка адресов выдуманная или подставная; очередь — подделка с правилами rq 2.12; платная
ступень — подделка со счётом обращений. Ниши, рынки, зоны и письменности разные, домены выдуманы,
IDN — в punycode.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.contacts import mx
from backend.features.contacts.provider import ProviderQuotaError
from backend.features.core.domain import AuditAction, ContactSource, ContactStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.ops import job_outcome
from backend.features.sales import (
    cleaning,
    deferred,
    segment_again,
    segment_jobs,
    segment_paid,
    segment_report,
    segment_run,
    segment_spend,
    verify_again,
)
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from backend.features.sales.segment_estimate import SALES_OFF
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel, Stage
from backend.features.sales.segment_queue import (
    SegmentContactQueue,
    paid_left,
    retry_at,
    term_reached,
)
from backend.features.sales.segment_rejudge import rejudge_job_id
from backend.features.sales.segment_run import Again, again
from backend.features.sales.verifier import FixtureVerifier
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.conftest import TEST_DSN
from tests.test_contacts_no_answer import Paid
from tests.test_sales_clean_api import _Job
from tests.test_sales_segment_rejudge import site
from tests.test_sales_segment_run import ANSWERS, PARTS, SHOP, Model, go, hypothesis, make_run, serp
from tests.test_sales_verify_again import Later

Candidate = SalesSegmentCandidateModel
#: Срок повтора не раньше этого (`SALES_SEGMENT_AGAIN_MIN`, умолчание).
LEAST = timedelta(minutes=30)

#: Сайты разных ниш, рынков, зон и письменностей — у каждого адрес на главной своим языком;
#: домены IDN — в punycode, как их хранит база: пекарня (.рф), стоматология (.de), книги (зона IDN
#: .امارات), йога (.com.au), чай (.jp).
BAKERY = "xn----7sbocojrfofidd7r.xn--p1ai"
CLINIC = "zahnarzt-sample.de"
BOOKS = "xn----zmcbedj7noabf.xn--mgbaam7a8h"
YOGA = "yoga-studio-sample.com.au"
TEA = "xn----ifuwe8csby996gnwb.jp"
HOMES = {
    BAKERY: "<p>Свежий хлеб каждый день. Пишите: <a href='mailto:zakaz@{host}'>почта</a></p>",
    CLINIC: "<p>Zahnarztpraxis — Termine: <a href='mailto:praxis@{host}'>E-Mail</a></p>",
    BOOKS: "<p dir='rtl'>كتب جديدة كل أسبوع — <a href='mailto:info@{host}'>البريد</a></p>",
    YOGA: "<p>Morning classes. Write to <a href='mailto:hello@{host}'>us</a></p>",
    TEA: "<p>日本茶の専門店。お問い合わせ: <a href='mailto:shop@{host}'>メール</a></p>",
}


class Web:
    """Сайты: главная с адресом своим языком; хост из `down` — обрыв соединения (не ответил)."""

    def __init__(self, down: set[str] | None = None) -> None:
        self.down = down or set()
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # Хост — как он ушёл в запрос: IDN — в punycode (`raw_host`), как его хранит база.
        host = request.url.raw_host.decode("ascii").removeprefix("www.")
        self.asked.append(host)
        if host in self.down:
            raise httpx.ConnectError("соединение не установилось", request=request)
        body = HOMES.get(host, "<p>{host}</p>").format(host=host)
        if request.url.path in ("", "/"):
            return httpx.Response(200, html=f"<html><body>{body}</body></html>", request=request)
        return httpx.Response(404, text="нет", request=request)


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS — у всех доменов почта есть; платной ступени поиска адреса нет."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    async def no_paid(_http: httpx.AsyncClient, _report: object) -> None:
        return None

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr("backend.features.contacts.search.paid_step", no_paid)


def _sites(monkeypatch: pytest.MonkeyPatch, found: Web) -> Web:
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(found)),
    )
    return found


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    return _sites(monkeypatch, Web())


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> Later:
    found = Later()
    monkeypatch.setattr(deferred, "sales_bulk_queue", lambda: found)
    return found


@pytest.fixture
def paid(monkeypatch: pytest.MonkeyPatch) -> Paid:
    """Платная ступень есть: включена у сбора, ключ задан; обращения — в счёт подделки."""
    found = Paid()

    async def step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return found

    monkeypatch.setattr("backend.features.contacts.search.paid_step", step)
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr("backend.config.contacts.HUNTER_API_KEY", "k-test")
    return found


def _ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


async def _accepted(session: AsyncSession, run: Any, host: str, **contact: Any) -> Candidate:
    return await site(session, run, host, lane=Lane.ACCEPT, **contact)


async def _done_run(session: AsyncSession, *, searches: int | None = None) -> Any:
    """Готовый прогон; `searches` — платных поисков адреса в смете запуска."""
    run = await make_run(session, await hypothesis(session))
    run.stage, run.finished_at = Stage.DONE, _ago(hours=3)
    if searches is not None:
        run.estimate = {**run.estimate, "hunter_searches": searches}
    await session.commit()
    return run


async def _fresh(session: AsyncSession, candidate: Candidate) -> Candidate:
    found = await session.get(Candidate, candidate.id, populate_existing=True)
    assert found is not None
    return found


async def _run(session: AsyncSession, run: Any) -> Any:
    found = await session.get(type(run), run.id, populate_existing=True)
    assert found is not None
    return found


# --- срок повтора: одно правило в Python и в SQL ---------------------------------------------------


def test_the_term_is_the_attempts_rule_but_not_sooner_than_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Первый срок у общего правила — «следующий проход»: сам повтор ждёт настройку (30 мин);
    второй и третий — день и неделя, как у доноров и рекламодателей."""
    attempted = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)

    assert [retry_at(attempted, tries) - attempted for tries in (0, 1, 2, 3, 4)] == [
        LEAST,
        LEAST,
        timedelta(days=1),
        timedelta(days=7),
        timedelta(days=7),
    ]
    monkeypatch.setattr("backend.config.sales.SEGMENT_AGAIN_MIN", 2 * 24 * 60)
    assert retry_at(attempted, 2) - attempted == timedelta(days=2)


#: Счёт проходов без ответа и минут после попытки — по краю каждой ступени расписания; домены —
#: разных зон.
EDGES = [
    (0, 29, "edge-a-sample.it"),
    (0, 31, "edge-b-sample.com.mx"),
    (1, 29, "xn--edge-c-sample-xyb.fr"),
    (1, 31, "edge-d-sample.co.kr"),
    (2, 24 * 60 - 1, "edge-e-sample.pl"),
    (2, 24 * 60 + 1, "edge-f-sample.nl"),
    (3, 7 * 24 * 60 - 1, "edge-g-sample.se"),
    (3, 7 * 24 * 60 + 1, "edge-h-sample.co.za"),
    (5, 7 * 24 * 60 + 1, "edge-i-sample.ca"),
]


async def test_the_sql_term_is_the_python_term(session: AsyncSession) -> None:
    """Срок у выборки (`term_reached`) и у задачи на срок (`retry_at`) — одно правило: по каждой
    ступени и по краю. Иначе задача приходила бы к сайтам, которых выборка ещё не берёт."""
    run = await _done_run(session)
    now = datetime.now(UTC)
    for tries, minutes, host in EDGES:
        await _accepted(
            session, run, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=tries,
            contact_attempted_at=now - timedelta(minutes=minutes),
        )  # fmt: skip

    reached = set(
        (
            await session.scalars(
                select(DomainModel.host)
                .join(Candidate, Candidate.domain_id == DomainModel.id)
                .where(Candidate.run_id == run.id, term_reached(now))
            )
        ).all()
    )

    expected = {
        host
        for tries, minutes, host in EDGES
        if retry_at(now - timedelta(minutes=minutes), tries) <= now
    }
    assert reached == expected
    assert expected == {
        "edge-b-sample.com.mx",
        "edge-d-sample.co.kr",
        "edge-f-sample.nl",
        "edge-h-sample.co.za",
        "edge-i-sample.ca",
    }


async def test_the_setting_holds_back_the_pass_and_the_count(session: AsyncSession) -> None:
    """Ревью 20.10: `SALES_SEGMENT_AGAIN_MIN` откладывал задачу на срок, но не выборку — первый
    «не ответил» (у общего правила — «следующий проход») брался «Найти адреса принятым» и считался
    в «ждут адреса» сразу. Теперь — одним условием с задачей."""
    run = await _done_run(session)
    await _accepted(
        session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(minutes=40),
    )  # fmt: skip
    await _accepted(
        session, run, BOOKS, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(minutes=2),
    )  # fmt: skip

    hosts = await SegmentContactQueue(session, run.id).pending_hosts()
    view = await segment_report.run_view(session, run.id)

    assert hosts == [BAKERY]
    assert view is not None
    assert view.waiting_address == 1


# --- срок после прохода — задачей очереди, и после сбоя тоже ----------------------------------------


async def test_a_silent_site_gets_its_term(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Проход прогона кончился, сайт не ответил поиску — задача на его срок, номер — от минуты."""
    _sites(monkeypatch, Web(down={PARTS}))
    run = await make_run(session, await hypothesis(session), mode=JudgeMode.ENFORCE)
    await go(session, run, serp(SHOP, PARTS), Model(ANSWERS))

    await segment_again.plan(session, run.id, now=datetime.now(UTC))

    parts = await session.scalar(
        select(Candidate).join(DomainModel).where(DomainModel.host == PARTS)
    )
    assert parts is not None
    assert (parts.contact_status, parts.contact_tries) == (ContactStatus.NO_ANSWER, 1)
    assert parts.contact_attempted_at is not None
    term = parts.contact_attempted_at + LEAST
    assert [(when, path, args) for when, path, args, _ in later.planned] == [
        (term, segment_again.TRIGGER, (run.id,))
    ]
    assert later.planned[0][3] == f"sales-segment-again-{run.id}-{term:%Y%m%dT%H%M}"


async def test_no_silent_sites_no_term(session: AsyncSession, web: Web, later: Later) -> None:
    run = await make_run(session, await hypothesis(session))
    await go(session, run, serp(SHOP), Model(ANSWERS))

    assert await segment_again.plan(session, run.id, now=datetime.now(UTC)) is None
    assert later.planned == []


async def test_every_pass_plans_the_next_terms_even_a_failed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ревью 20.10: проход, кончившийся сбоем или остановкой, срока не ставил — повтор замолкал.
    Теперь сроки молчавших сайтов и лидов «ещё проверяет» ставит задача прохода после любого
    исхода; после сбоя — не раньше `SALES_SEGMENT_AGAIN_MIN`. Сбой повтора стадию не трогает."""
    planned: list[tuple[str, timedelta]] = []
    failed: list[int] = []
    stopped: list[int] = []

    async def plan_sites(_s: AsyncSession, _run: int, *, now: datetime, soonest: timedelta) -> None:
        planned.append(("сайты", soonest))

    async def plan_leads(_s: AsyncSession, *, now: datetime, soonest: timedelta) -> None:
        planned.append(("лиды", soonest))

    async def done(_factory: object, run_id: int, _what: Again) -> dict[str, Any]:
        return {"run": run_id}

    async def broken(_factory: object, _run_id: int, _what: Again) -> dict[str, Any]:
        raise ConnectionError("база мигнула")

    async def again_failed(_factory: object, run_id: int) -> None:
        failed.append(run_id)

    async def stop(_factory: object, run_id: int, _words: str) -> None:
        stopped.append(run_id)

    monkeypatch.setattr("backend.config.sales.ENABLED", True)
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    monkeypatch.setattr(segment_again, "plan", plan_sites)
    monkeypatch.setattr(verify_again, "plan", plan_leads)
    monkeypatch.setattr(segment_jobs, "_again_failed", again_failed)
    monkeypatch.setattr(segment_jobs, "_stop", stop)
    monkeypatch.setattr(segment_jobs, "get_current_job", lambda: None)  # последняя попытка

    monkeypatch.setattr(segment_jobs, "_again", done)
    assert await segment_jobs.run_once(7, Again.DUE) == {"run": 7}
    monkeypatch.setattr(segment_jobs, "_again", broken)
    with pytest.raises(ConnectionError):
        await segment_jobs.run_once(7, Again.DUE)

    assert planned == [
        ("сайты", deferred.SOONEST),
        ("лиды", deferred.SOONEST),
        ("сайты", LEAST),
        ("лиды", LEAST),
    ]
    assert (failed, stopped) == ([7], [])


async def test_the_retry_pass_has_its_pulse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ревью 20.10: повтор шёл без пульса — экран не отличал его от мёртвого."""
    beats: list[int] = []

    async def pulse(_factory: object, run_id: int, **_kw: object) -> None:
        beats.append(run_id)
        await asyncio.sleep(3600)

    async def retried(_s: AsyncSession, run_id: int, _v: object, _w: Again) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"run": run_id}

    monkeypatch.setattr(segment_jobs, "pulse", pulse)
    monkeypatch.setattr(segment_jobs, "again", retried)
    engine = create_async_engine(TEST_DSN)
    try:
        found = await segment_jobs._again(async_sessionmaker(engine), 7, Again.DUE)
    finally:
        await engine.dispose()

    assert (found, beats) == ({"run": 7}, [7])


# --- задача на срок ставит проход ---------------------------------------------------------------------


async def _silent_sites(session: AsyncSession, run: Any) -> dict[str, Candidate]:
    """Принятые сайты прогона в разных исходах поиска."""
    return {
        # Не ответил час назад первый раз — срок (30 мин) настал.
        "due": await _accepted(
            session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER,
            contact_tries=1, contact_attempted_at=_ago(hours=1),
        ),
        # Не ответил второй раз час назад — повтор через день.
        "later": await _accepted(
            session, run, CLINIC, contact_status=ContactStatus.NO_ANSWER,
            contact_tries=2, contact_attempted_at=_ago(hours=1),
        ),
        # Квота платной ступени — «не спросили»: повтор — с платной ступенью.
        "quota": await _accepted(
            session, run, BOOKS, contact_status=ContactStatus.NO_QUOTA,
            contact_attempted_at=_ago(hours=2),
        ),
        # Принят человеком, адрес ещё не искали — первый поиск кнопкой.
        "fresh": await _accepted(session, run, YOGA),
        # Адреса на сайте нет — до срока годности не ищется.
        "none": await _accepted(
            session, run, TEA, contact_status=ContactStatus.NOT_FOUND,
            contact_attempted_at=_ago(days=2),
        ),
    }  # fmt: skip


async def test_the_term_puts_the_pass_by_term_under_the_run_number(
    session: AsyncSession, later: Later
) -> None:
    """Проход по сроку — задача прогона под его номером, как «Продолжить»: один проход за раз.
    Поставлен — прогон идёт (пуст конец, номер задачи), в журнале — «автоматически, по сроку».
    Номер занят — пропуск и новый срок: идущий проход мог упасть, не поставив своего."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    now = datetime.now(UTC)

    first = await segment_jobs.put_due(session, later, run.id, now=now)
    second = await segment_jobs.put_due(session, later, run.id, now=now)

    assert first == {"run": run.id, "job_id": segment_job_id(run.id)}
    assert (second["skipped"], later.enqueued) == (
        segment_jobs.GOING_NOW,
        [(SEGMENT_JOB, (run.id, Again.DUE.value), segment_job_id(run.id))],
    )
    assert [(when, path) for when, path, *_ in later.planned] == [
        (now + LEAST, segment_again.TRIGGER)
    ]
    refreshed = await _run(session, run)
    assert (refreshed.job_id, refreshed.finished_at) == (segment_job_id(run.id), None)
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert (journal.user_id, journal.details) == (
        None,
        {"этап": "sales", "действие": segment_jobs.AUTO, "прогон": run.id, "сайтов": 1},
    )


async def test_the_term_with_nothing_due_puts_nothing(session: AsyncSession, later: Later) -> None:
    """Ревью 20.10: пустой проход мигал «идёт» и сдвигал конец прогона. Повторять некому —
    прохода нет, конец прежний, задача — на срок впереди."""
    run = await _done_run(session)
    ahead = await _accepted(
        session, run, CLINIC, contact_status=ContactStatus.NO_ANSWER, contact_tries=2,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    finished = run.finished_at

    found = await segment_jobs.put_due(session, later, run.id, now=datetime.now(UTC))

    assert found == {"run": run.id, "skipped": segment_jobs.NOTHING_DUE}
    assert later.enqueued == []
    assert ahead.contact_attempted_at is not None
    assert [when for when, *_ in later.planned] == [ahead.contact_attempted_at + timedelta(days=1)]
    assert (await _run(session, run)).finished_at == finished


@pytest.mark.parametrize("alive", [True, None])
async def test_the_term_waits_for_a_rejudge(
    session: AsyncSession, later: Later, monkeypatch: pytest.MonkeyPatch, alive: bool | None
) -> None:
    """Ревью 20.10: «Продолжить» посреди пересуда отказывает, а проход по сроку шёл. Теперь —
    пропуск и новый срок; очередь не ответила о пересуде — тоже пропуск: неизвестность — не
    «не идёт»."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    number = rejudge_job_id(run.id)
    later.known[number] = _Job(number, "rejudge", later.connection)
    later.known[number].status = JobStatus.STARTED
    monkeypatch.setattr(segment_jobs, "job_alive", lambda _job_id, _redis: alive)
    now = datetime.now(UTC)

    found = await segment_jobs.put_due(session, later, run.id, now=now)

    assert found["skipped"] == segment_jobs.REJUDGING
    assert later.enqueued == []
    assert [when for when, *_ in later.planned] == [now + LEAST]


def test_the_term_with_sales_off_spends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = Later()
    monkeypatch.setattr(segment_jobs, "sales_bulk_queue", lambda: jobs)
    monkeypatch.setattr(segment_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr("backend.config.sales.ENABLED", False)

    assert segment_jobs.due_again(41) == {"run": 41, "skipped": SALES_OFF}
    assert (jobs.enqueued, jobs.planned) == ([], [])


def test_the_term_job_path_is_importable_by_the_worker() -> None:
    module, _, name = segment_again.TRIGGER.rpartition(".")
    assert (module, name) == ("backend.features.sales.segment_jobs", "due_again")


def test_the_term_job_is_named_in_words_on_the_screen() -> None:
    """Строка задачи на экране говорит, что это за задача, а не путь функции; задача — продаж."""
    assert job_outcome.KINDS[segment_again.TRIGGER] == "срок повтора адреса продаж"
    assert job_outcome.sales_job("x", segment_again.TRIGGER, None)


# --- проход по сроку и «Повторить сейчас» ---------------------------------------------------------


async def _ready(session: AsyncSession, run: Any) -> Candidate:
    """Принят, адрес на домене уже есть (нашли доноры): лид заведёт проход, поиск не нужен."""
    found = await _accepted(session, run, "atelier-sample.fr")
    session.add(
        ContactModel(
            domain_id=found.domain_id,
            email="atelier@atelier-sample.fr",
            source=ContactSource.PAGE,
        )
    )
    await session.commit()
    return found


async def _leads_of(session: AsyncSession, run: Any) -> dict[str, str]:
    rows = await session.execute(
        select(DomainModel.host, SalesLeadModel.email)
        .join(Candidate, Candidate.domain_id == DomainModel.id)
        .join(SalesLeadModel, SalesLeadModel.id == Candidate.lead_id)
        .where(Candidate.run_id == run.id)
    )
    return dict(rows.tuples().all())


async def test_the_pass_by_term_asks_only_the_silent_sites_whose_term_came(
    session: AsyncSession, web: Web, later: Later
) -> None:
    """По сроку — только «не ответил», чей срок настал. «Не спросили» (квота) без платной ступени
    не берётся (ревью 20.10: повтор отправлял его в «адреса нет» на полгода); первый поиск
    принятому — тоже нет: он стоит денег, и его запускает человек. Лиды — только из найденного."""
    run = await _done_run(session)
    found = await _silent_sites(session, run)
    ready_domain = await _ready(session, run)
    quota_before = found["quota"].contact_attempted_at

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert set(web.asked) == {BAKERY}
    assert await _leads_of(session, run) == {BAKERY: f"zakaz@{BAKERY}"}
    quota = await _fresh(session, found["quota"])
    assert (quota.contact_status, quota.contact_attempted_at) == (
        ContactStatus.NO_QUOTA,
        quota_before,
    )
    assert (await _fresh(session, found["later"])).contact_status is ContactStatus.NO_ANSWER
    assert (await _fresh(session, found["fresh"])).contact_status is None
    assert (await _fresh(session, ready_domain)).lead_id is None
    refreshed = await _run(session, run)
    assert (refreshed.stage, refreshed.finished_at is not None) == (Stage.DONE, True)
    assert segment_run.AGAIN_KEY not in refreshed.report


async def test_retry_now_takes_only_the_sites_whose_term_came(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Ревью 20.10: кнопка брала всех, и каждое нажатие было проходом без ответа — три нажатия, и
    «адреса нет» на полгода. Срок, который не настал, кнопка не трогает, сколько ни жми."""
    silent = _sites(monkeypatch, Web(down={BAKERY}))
    run = await _done_run(session)
    waiting = await _accepted(
        session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(minutes=5),
    )  # fmt: skip

    for _ in range(4):
        await again(session, run.id, FixtureVerifier(), Again.ADDRESSES)

    found = await _fresh(session, waiting)
    assert (found.contact_status, found.contact_tries) == (ContactStatus.NO_ANSWER, 1)
    assert silent.asked == []


@pytest.mark.parametrize("unasked", [ContactStatus.NO_QUOTA, ContactStatus.PAID_OFF])
async def test_retry_now_asks_the_unasked_only_with_the_paid_step(
    session: AsyncSession,
    web: Web,
    later: Later,
    monkeypatch: pytest.MonkeyPatch,
    unasked: ContactStatus,
) -> None:
    """«Не спросили» платный сервис — кнопкой, только когда платная ступень у повтора правда есть:
    без неё бесплатные ступени (уже пройденные) отправили бы сайт в «адреса нет». Квота и
    недоступная ступень (выключена, ключ не принят) — одно «не спросили»."""
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", False)
    run = await _done_run(session, searches=5)
    quota = await _accepted(
        session, run, BOOKS, contact_status=unasked,
        contact_attempted_at=_ago(hours=2),
    )  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.ADDRESSES)
    assert (web.asked, (await _fresh(session, quota)).contact_status) == (
        [],
        unasked,
    )

    _paid_ready(monkeypatch, Paid())
    await again(session, run.id, FixtureVerifier(), Again.ADDRESSES)
    assert set(web.asked) == {BOOKS}
    assert await _leads_of(session, run) == {BOOKS: f"info@{BOOKS}"}


def _paid_ready(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    """Платная ступень есть: включена у сбора, ключ задан, поиск получает этого провайдера."""

    async def step(_http: httpx.AsyncClient, _report: object) -> object:
        return provider

    monkeypatch.setattr("backend.features.contacts.search.paid_step", step)
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr("backend.config.contacts.HUNTER_API_KEY", "k-test")


class Refusing:
    """Платная ступень, у которой кончилась квота: каждый запрос — отказ (провайдер его не
    засчитывает)."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def quota(self) -> Any:
        raise AssertionError("остаток узнаёт `paid_step`, подменённый в тесте")

    async def find_emails(self, host: str) -> list[Any]:
        self.calls.append(host)
        raise ProviderQuotaError("квота исчерпана")


async def _last_try(session: AsyncSession, run: Any, host: str) -> Candidate:
    """Не ответил три прохода подряд, неделю назад: этот проход — последний, в нём платят."""
    return await _accepted(
        session, run, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=3,
        contact_attempted_at=_ago(days=8),
    )  # fmt: skip


async def test_paid_search_by_term_only_on_the_last_pass_and_only_within_the_estimate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Платная ступень по сроку — только сайту, для которого проход последний, и только когда
    поиск был в смете: остальным сроком — бесплатные ступени. Запрос — в отчёт прогона."""
    silent = _sites(monkeypatch, Web(down={BAKERY, CLINIC}))
    run = await _done_run(session, searches=5)
    await _accepted(
        session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    await _last_try(session, run, CLINIC)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert paid.calls == [CLINIC]
    assert set(silent.asked) >= {BAKERY, CLINIC}
    counters = (await _run(session, run)).report["contacts"]["counters"]
    assert counters["provider_entered"] == 1


async def test_no_paid_search_in_the_estimate_no_hunter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Смета запуска без платного поиска (`hunter_searches` = 0) — Hunter не спрошен и в
    последний проход."""
    _sites(monkeypatch, Web(down={CLINIC}))
    run = await _done_run(session, searches=0)
    await _last_try(session, run, CLINIC)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert paid.calls == []


async def test_the_paid_search_by_term_stops_at_the_estimate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Ревью 20.10: платный поиск по сроку разрешал флаг «поиск был в смете», а не остаток. Теперь
    — не больше, чем смета минус уже принятые провайдером запросы прогона; остальным — бесплатные
    ступени."""
    silent = _sites(monkeypatch, Web(down={CLINIC, TEA}))
    run = await _done_run(session, searches=2)
    run.report = {"contacts": {"walked": 9, "counters": {"provider_entered": 1}}}
    await session.commit()
    await _last_try(session, run, CLINIC)
    await _last_try(session, run, TEA)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert paid.calls == [CLINIC]
    assert set(silent.asked) >= {CLINIC, TEA}
    counters = (await _run(session, run)).report["contacts"]["counters"]
    assert counters["provider_entered"] == 2


async def test_a_crash_mid_search_does_not_pay_past_the_estimate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Ревью 20.10, второй круг: запросы поиска попадали в отчёт прогона только в конце прохода, а
    поиск платит и коммитит по пачкам — проход упал между пачками, и повтор задачи считал остаток
    сметы целым и платил второй раз. Теперь доля остатка резервируется до куска своим коммитом
    (`segment_spend.pending`): смета 1 — и после сбоя платный запрос один."""
    silent = _sites(monkeypatch, Web(down={CLINIC, TEA}))
    run = await _done_run(session, searches=1)
    await _last_try(session, run, CLINIC)
    await _last_try(session, run, TEA)
    monkeypatch.setattr("backend.features.contacts.search.BATCH", 1)  # пачка — один сайт
    real_commit = AsyncSession.commit
    crashed: list[bool] = []

    async def crash_after_the_paid_batch(self: AsyncSession) -> None:
        await real_commit(self)
        if paid.calls and not crashed:
            crashed.append(True)
            raise ConnectionError("воркер упал между пачками")

    monkeypatch.setattr(AsyncSession, "commit", crash_after_the_paid_batch)
    with pytest.raises(ConnectionError):
        await again(session, run.id, FixtureVerifier(), Again.DUE)
    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    await again(session, run.id, FixtureVerifier(), Again.DUE)  # повтор задачи rq

    assert paid.calls == [CLINIC]
    assert set(silent.asked) >= {CLINIC, TEA}
    refreshed = await _run(session, run)
    # Резерв упавшего куска сведён следующим проходом: запрос принят, но в счёт не попал.
    assert (
        segment_spend.pending(refreshed.report),
        segment_spend.unrecorded(refreshed.report),
    ) == (
        0,
        1,
    )


async def test_a_silent_site_does_not_take_the_budget_of_one_that_answers(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Ревью 20.10, второй круг: бюджет делился по сайтам — молчащий сайт занимал место в платной
    части, не заплатив, а ответивший без адреса уходил в бесплатную и в «адреса нет» при живом
    остатке. Теперь кусок не больше остатка, и остаток, который молчащий не потратил, получает
    следующий."""
    _sites(monkeypatch, Web(down={BAKERY}))
    run = await _done_run(session, searches=1)
    await _accepted(
        session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    await _accepted(
        session, run, "plain-sample.nl", contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert paid.calls == ["plain-sample.nl"]


async def test_without_the_paid_step_the_unasked_stay_unasked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Ревью 20.10, третий круг: квота Hunter кончилась (поиск не получил платной ступени), а
    кнопка кусками прошла всех «не спросили» бесплатными ступенями — и похоронила их в «адреса
    нет» на полгода. Теперь платная ступень проверяется до куска: её нет — «не спросили» не
    трогаются."""
    web = _sites(monkeypatch, Web())
    monkeypatch.setattr("backend.config.sales.SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr("backend.config.contacts.HUNTER_API_KEY", "k-test")
    run = await _done_run(session, searches=2)
    unasked = [
        await _accepted(
            session, run, f"quota{n}-sample.example.test", contact_status=ContactStatus.NO_QUOTA,
            contact_attempted_at=_ago(hours=2),
        )
        for n in range(5)
    ]  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.ADDRESSES)

    statuses = {(await _fresh(session, site)).contact_status for site in unasked}
    assert (statuses, web.asked) == ({ContactStatus.NO_QUOTA}, [])


async def test_a_crash_does_not_freeze_the_whole_estimate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, paid: Paid
) -> None:
    """Ревью 20.10, третий круг: кусок резервировал весь остаток сметы, и после сбоя посреди куска
    резерв замораживал её навсегда. Теперь кусок — не больше сайтов к поиску и пачки поиска, а
    следующий проход сводит резерв по журналу расхода: смета 10, сайт один — свободно не меньше 9."""
    _sites(monkeypatch, Web(down={CLINIC}))
    run = await _done_run(session, searches=10)
    await _last_try(session, run, CLINIC)
    real = segment_paid.contacts_search.search_contacts

    async def crash(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise ConnectionError("воркер упал посреди куска")

    monkeypatch.setattr(segment_paid.contacts_search, "search_contacts", crash)
    with pytest.raises(ConnectionError):
        await again(session, run.id, FixtureVerifier(), Again.DUE)
    crashed = await _run(session, run)
    assert segment_spend.pending(crashed.report) == 1
    monkeypatch.setattr(segment_paid.contacts_search, "search_contacts", real)

    await again(session, run.id, FixtureVerifier(), Again.DUE)  # следующий проход прогона

    after = await _run(session, run)
    assert segment_spend.pending(after.report) == 0
    assert paid_left(after.report, after.estimate) >= 9


async def test_a_refusal_leaves_the_rest_for_the_next_pass(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Ревью 20.10, третий круг: после отказа провайдера остальные сайты шли бесплатными ступенями,
    и ответивший без адреса получал «адреса нет» при живом остатке. Теперь проход кончает платную
    часть и остальных не трогает — их возьмёт следующий."""
    _sites(monkeypatch, Web())
    refusing = Refusing()
    _paid_ready(monkeypatch, refusing)
    run = await _done_run(session, searches=1)
    hosts = [f"plain{n}-sample.example.test" for n in range(3)]
    for host in hosts:
        await _accepted(
            session, run, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
            contact_attempted_at=_ago(hours=1),
        )  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    rows = await session.execute(
        select(DomainModel.host, Candidate.contact_status, Candidate.contact_tries)
        .join(Candidate, Candidate.domain_id == DomainModel.id)
        .where(Candidate.run_id == run.id)
    )
    found = {host: (status, tries) for host, status, tries in rows.tuples().all()}
    assert refusing.calls == [hosts[0]]
    assert found == {
        hosts[0]: (ContactStatus.NO_QUOTA, 1),  # «не спросили»: повтор — с платной ступенью
        hosts[1]: (ContactStatus.NO_ANSWER, 1),
        hosts[2]: (ContactStatus.NO_ANSWER, 1),
    }


async def test_a_stopped_run_keeps_its_stage_words_and_progress(
    session: AsyncSession, web: Web, later: Later
) -> None:
    """Повтор стадию, причину остановки и ход прохода не трогает (ревью 20.10: ход перетирался, и
    «остановлен на этапе «судья»: 40 из 60» становилось «адреса»); его ход — своим ключом и
    снимается в конце."""
    run = await make_run(session, await hypothesis(session))
    progress = {"stage": "judge", "done": 40, "total": 60, "pass_started_at": "2026-10-20T07:00:00"}
    run.stage, run.error = Stage.STOPPED, "судья остановлен: потолок модели на сегодня выбран"
    run.report = {"progress": progress}
    await session.commit()
    await _silent_sites(session, run)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    refreshed = await _run(session, run)
    assert (refreshed.stage, refreshed.error) == (
        Stage.STOPPED,
        "судья остановлен: потолок модели на сегодня выбран",
    )
    assert refreshed.report["progress"] == progress
    assert segment_run.AGAIN_KEY not in refreshed.report


async def test_a_crash_and_the_queue_retry_keep_the_stage(
    session: AsyncSession, web: Web, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревью 20.10: повтор задачи после сбоя читал стадию, которую записала упавшая попытка, —
    остановленный прогон становился готовым. Теперь стадии повтор не пишет вовсе."""
    run = await make_run(session, await hypothesis(session))
    run.stage, run.error = Stage.STOPPED, "судья остановлен: потолок модели на сегодня выбран"
    await session.commit()
    await _silent_sites(session, run)
    calls = {"n": 0}
    real = segment_run._retry_walk

    async def broken(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("база мигнула")
        return await real(*args, **kwargs)

    monkeypatch.setattr(segment_run, "_retry_walk", broken)
    with pytest.raises(ConnectionError):
        await again(session, run.id, FixtureVerifier(), Again.DUE)
    await session.rollback()
    crashed = await _run(session, run)
    assert (crashed.stage, crashed.report[segment_run.AGAIN_KEY]["what"]) == (Stage.STOPPED, "due")

    await again(session, run.id, FixtureVerifier(), Again.DUE)  # повтор задачи rq

    after = await _run(session, run)
    assert (after.stage, after.error) == (
        Stage.STOPPED,
        "судья остановлен: потолок модели на сегодня выбран",
    )
    assert segment_run.AGAIN_KEY not in after.report
    assert await _leads_of(session, run) == {BAKERY: f"zakaz@{BAKERY}"}


async def test_the_queue_retry_makes_the_leads_of_the_first_try(
    session: AsyncSession, web: Web, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Первая попытка нашла адрес и упала на лидах: повтор задачи заводит лида из найденного ею —
    начало повтора записано, а искать уже некому."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    calls = {"n": 0}
    real = segment_run.make_leads

    async def broken(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("база мигнула")
        return await real(*args, **kwargs)

    monkeypatch.setattr(segment_run, "make_leads", broken)
    with pytest.raises(ConnectionError):
        await again(session, run.id, FixtureVerifier(), Again.DUE)
    await session.rollback()
    asked = list(web.asked)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert await _leads_of(session, run) == {BAKERY: f"zakaz@{BAKERY}"}
    assert (set(asked), web.asked) == ({BAKERY}, asked)  # второй раз сайт не спрашивали


async def test_an_empty_pass_by_term_changes_nothing(
    session: AsyncSession, web: Web, later: Later
) -> None:
    """Ревью 20.10: проход, которому некого искать, писал ход и сдвигал конец прогона."""
    run = await _done_run(session)
    run.report = {"progress": {"stage": "leads", "done": 3, "total": 3}, "leads": {"created": 3}}
    await session.commit()
    await _accepted(
        session, run, CLINIC, contact_status=ContactStatus.NO_ANSWER, contact_tries=2,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    finished, report = run.finished_at, dict(run.report)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    refreshed = await _run(session, run)
    assert (refreshed.report, refreshed.finished_at) == (report, finished)
    assert web.asked == []


async def _lead(
    session: AsyncSession, candidate: Candidate, email: str, **fields: Any
) -> SalesLeadModel:
    lead = SalesLeadModel(
        hypothesis_id=candidate.hypothesis_id,
        domain_id=candidate.domain_id,
        email=email,
        source=LeadSource.COLLECTED,
        status=fields.pop("status", LeadStatus.NEW),
        **fields,
    )
    session.add(lead)
    await session.flush()
    candidate.lead_id = lead.id
    await session.commit()
    return lead
