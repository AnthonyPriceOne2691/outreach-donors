"""Повтор поиска адреса сайтам прогона, которые не ответили, — сам, по сроку; «ждут адреса» и
кнопка — условием прохода; «Повторить сейчас» и строки ожидания в отчёте прогона.

Разбор прогона сбора: проход адресов ставили только запуск и «Продолжить», а «ждут адреса» брал
лишь не искавшиеся — сайт с наступившим сроком повтора никто не искал и кнопки он не получал.
Здесь: срок после прохода — задачей очереди (`enqueue_at`), задача на срок ставит проход под
номером прогона, проход по сроку ищет только тех, кто ждёт повтора, и заводит лиды только из
найденного им; «Повторить сейчас» — не дожидаясь срока; отчёт называет, что ждёт и когда.

Сайты — `httpx.MockTransport`: главная с адресом или обрыв соединения; DNS — «почта есть»;
проверка адресов выдуманная; очередь — подделка с правилами rq 2.12. Ниши, рынки и письменности
разные, домены выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.api.sales import segment as segment_api
from backend.config.judge import JudgeMode
from backend.features.contacts import mx
from backend.features.core.domain import AuditAction, ContactSource, ContactStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.sales import (
    cleaning,
    deferred,
    segment_again,
    segment_jobs,
    segment_report,
    segment_waits,
)
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from backend.features.sales.segment_estimate import SALES_OFF
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel, Stage
from backend.features.sales.segment_queue import SegmentContactQueue, retry_at
from backend.features.sales.segment_run import Again, again
from backend.features.sales.verifier import FixtureVerifier
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_api import RUNS, headers, jobs
from tests.test_sales_segment_rejudge import site
from tests.test_sales_segment_run import ANSWERS, PARTS, SHOP, Model, go, hypothesis, make_run, serp
from tests.test_sales_verify_again import Later

__all__ = ["headers", "jobs"]  # вход и подделка очереди — те же, что у маршрутов сбора

Candidate = SalesSegmentCandidateModel

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
    monkeypatch.setattr("backend.features.contacts.search._paid_step", no_paid)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    found = Web()
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(found)),
    )
    return found


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> Later:
    found = Later()
    monkeypatch.setattr(deferred, "sales_bulk_queue", lambda: found)
    return found


def _ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


async def _accepted(session: AsyncSession, run: Any, host: str, **contact: Any) -> Candidate:
    return await site(session, run, host, lane=Lane.ACCEPT, **contact)


async def _done_run(session: AsyncSession) -> Any:
    run = await make_run(session, await hypothesis(session))
    run.stage, run.finished_at = Stage.DONE, _ago(hours=3)
    await session.commit()
    return run


async def _fresh(session: AsyncSession, candidate: Candidate) -> Candidate:
    found = await session.get(Candidate, candidate.id, populate_existing=True)
    assert found is not None
    return found


# --- срок повтора -----------------------------------------------------------------------------------


def test_the_term_is_the_attempts_rule_but_not_sooner_than_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Первый срок у общего правила — «следующий проход»: сам повтор ждёт настройку (30 мин);
    второй и третий — день и неделя, как у доноров и рекламодателей."""
    attempted = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)

    assert [retry_at(attempted, tries) - attempted for tries in (0, 1, 2, 3, 4)] == [
        timedelta(minutes=30),
        timedelta(minutes=30),
        timedelta(days=1),
        timedelta(days=7),
        timedelta(days=7),
    ]
    monkeypatch.setattr("backend.config.sales.SEGMENT_AGAIN_MIN", 2 * 24 * 60)
    assert retry_at(attempted, 2) - attempted == timedelta(days=2)


async def test_a_silent_site_gets_its_term_after_the_pass(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Проход прогона кончился, сайт не ответил поиску — очередь получает задачу на его срок."""
    silent = Web(down={PARTS})
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(silent)),
    )
    run = await make_run(session, await hypothesis(session), mode=JudgeMode.ENFORCE)

    await go(session, run, serp(SHOP, PARTS), Model(ANSWERS))

    parts = await session.scalar(
        select(Candidate).join(DomainModel).where(DomainModel.host == PARTS)
    )
    assert parts is not None
    assert (parts.contact_status, parts.contact_tries) == (ContactStatus.NO_ANSWER, 1)
    assert parts.contact_attempted_at is not None
    term = parts.contact_attempted_at + timedelta(minutes=30)
    assert [(when, path, args) for when, path, args, _ in later.planned] == [
        (term, segment_again.TRIGGER, (run.id,))
    ]
    assert later.planned[0][3] == f"sales-segment-again-{run.id}-{term:%Y%m%dT%H%M}"


async def test_no_silent_sites_no_term(session: AsyncSession, web: Web, later: Later) -> None:
    run = await make_run(session, await hypothesis(session))

    await go(session, run, serp(SHOP), Model(ANSWERS))

    assert later.planned == []


# --- задача на срок -------------------------------------------------------------------------------


@pytest.fixture
def bulk(monkeypatch: pytest.MonkeyPatch) -> Later:
    found = Later()
    monkeypatch.setattr(segment_jobs, "sales_bulk_queue", lambda: found)
    monkeypatch.setattr(segment_jobs, "setup_logging", lambda: None)
    return found


def test_the_term_puts_the_pass_by_term_under_the_run_number(
    bulk: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проход по сроку — задача прогона под его номером, как «Продолжить»: один проход за раз.
    Номер держит живой проход — пропуск: следующий срок поставит он сам."""
    monkeypatch.setattr("backend.config.sales.ENABLED", True)

    first = segment_jobs.due_again(41)
    second = segment_jobs.due_again(41)

    assert first == {"run": 41, "job_id": segment_job_id(41)}
    assert second == {"run": 41, "skipped": segment_jobs.GOING_NOW}
    assert bulk.enqueued == [(SEGMENT_JOB, (41, Again.DUE.value), segment_job_id(41))]


def test_the_term_with_sales_off_spends_nothing(
    bulk: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.config.sales.ENABLED", False)

    assert segment_jobs.due_again(41) == {"run": 41, "skipped": SALES_OFF}
    assert bulk.enqueued == []


def test_the_term_job_path_is_importable_by_the_worker() -> None:
    module, _, name = segment_again.TRIGGER.rpartition(".")
    assert (module, name) == ("backend.features.sales.segment_jobs", "due_again")


# --- проход по сроку и «Повторить сейчас» ---------------------------------------------------------


async def _silent_sites(session: AsyncSession, run: Any) -> dict[str, Candidate]:
    """Принятые сайты прогона в разных исходах поиска."""
    return {
        # Не ответил час назад первый раз — срок общего правила настал («следующий проход»).
        "due": await _accepted(
            session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER,
            contact_tries=1, contact_attempted_at=_ago(hours=1),
        ),
        # Не ответил второй раз час назад — повтор через день.
        "later": await _accepted(
            session, run, CLINIC, contact_status=ContactStatus.NO_ANSWER,
            contact_tries=2, contact_attempted_at=_ago(hours=1),
        ),
        # Квота платной ступени — «не спросили», повтор следующим проходом.
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


async def test_the_pass_by_term_asks_only_the_sites_waiting_for_a_retry(
    session: AsyncSession, web: Web, later: Later
) -> None:
    """По сроку — только те, кому поиск уже шёл и чей срок настал; первый поиск принятому — нет:
    он стоит денег, и его запускает человек. Лиды — только из найденного этим проходом."""
    run = await _done_run(session)
    found = await _silent_sites(session, run)
    ready_domain = await _ready(session, run)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    assert set(web.asked) == {BAKERY, BOOKS}
    assert await _leads_of(session, run) == {
        BAKERY: f"zakaz@{BAKERY}",
        BOOKS: f"info@{BOOKS}",
    }
    assert (await _fresh(session, found["later"])).contact_status is ContactStatus.NO_ANSWER
    assert (await _fresh(session, found["fresh"])).contact_status is None
    assert (await _fresh(session, ready_domain)).lead_id is None
    refreshed = await session.get(type(run), run.id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.stage, refreshed.finished_at is not None) == (Stage.DONE, True)
    # Следующий срок — у сайта, чей повтор через день.
    later_site = await _fresh(session, found["later"])
    assert later_site.contact_attempted_at is not None
    assert [when for when, *_ in later.planned] == [
        later_site.contact_attempted_at + timedelta(days=1)
    ]


async def test_retry_now_does_not_wait_for_the_term(
    session: AsyncSession, web: Web, later: Later
) -> None:
    run = await _done_run(session)
    found = await _silent_sites(session, run)

    await again(session, run.id, FixtureVerifier(), Again.ADDRESSES)

    assert set(web.asked) == {BAKERY, CLINIC, BOOKS}
    assert (await _fresh(session, found["later"])).contact_status is ContactStatus.FOUND
    assert (await _fresh(session, found["fresh"])).contact_status is None
    assert later.planned == []  # молчавших не осталось


async def test_a_stopped_run_stays_stopped_with_its_words(
    session: AsyncSession, web: Web, later: Later
) -> None:
    run = await make_run(session, await hypothesis(session))
    run.stage, run.error = Stage.STOPPED, "судья остановлен: потолок модели на сегодня выбран"
    await session.commit()
    await _silent_sites(session, run)

    await again(session, run.id, FixtureVerifier(), Again.DUE)

    refreshed = await session.get(type(run), run.id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.stage, refreshed.error) == (
        Stage.STOPPED,
        "судья остановлен: потолок модели на сегодня выбран",
    )


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


async def test_retry_of_cleaning_takes_the_waiting_leads_of_the_run(
    session: AsyncSession, later: Later
) -> None:
    """«Повторить сейчас» у «ждут очистки» — очистка ждущих лидов прогона тем же условием, что у
    прохода очистки: новый — проверяется, первый `unknown` неделю не трогается, готовый — тоже."""
    run = await _done_run(session)
    waiting = await _lead(
        session,
        await _accepted(session, run, BAKERY),
        f"zakaz@{BAKERY}",
        cleaning_note="проверка не выполнена: сеть до провайдера не дошла",
    )
    checking = await _lead(
        session,
        await _accepted(session, run, CLINIC),
        f"pending@{CLINIC}",
        verify_tries=1,
        verify_again_at=datetime.now(UTC) + timedelta(minutes=12),
    )
    unknown = await _lead(
        session,
        await _accepted(session, run, BOOKS),
        f"info@{BOOKS}",
        verification_status="fixture:unknown",
        verified_at=_ago(days=1),
    )
    ready = await _lead(
        session, await _accepted(session, run, YOGA), f"hello@{YOGA}",
        status=LeadStatus.READY, verification_status="fixture:valid",
    )  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.CLEANING)

    statuses = {
        lead.id: (lead.status, lead.verify_tries)
        for lead in (await session.scalars(select(SalesLeadModel))).all()
    }
    assert statuses[waiting.id] == (LeadStatus.READY, 0)
    assert statuses[checking.id] == (LeadStatus.NEW, 2)
    assert statuses[unknown.id] == (LeadStatus.NEW, 0)
    assert statuses[ready.id] == (LeadStatus.READY, 0)
    refreshed = await session.get(type(run), run.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.report["leads"]["cleaning"]["checked"] == 2


# --- отчёт: «ждут адреса» условием прохода и строки ожидания ----------------------------------------


async def test_waiting_address_is_the_condition_of_the_pass(
    session: AsyncSession, web: Web
) -> None:
    """«Найти адреса принятым · N» — тем же условием, что у прохода: не искавшиеся, с наступившим
    сроком повтора и «не спросили», и лид из адреса, что уже есть на домене; не ответивший с
    повтором через день и «адреса нет» в счёт не входят."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    await _ready(session, run)

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    pass_takes = await SegmentContactQueue(session, run.id).pending_count()
    assert (pass_takes, view.waiting_address) == (3, 4)  # due, quota, fresh — и лид из адреса
    # «Без адреса — почему» — по исходу поиска, как было.
    assert sorted(words for words, _ in view.no_address) == sorted(
        [
            "сайт не ответил — повтор по сроку",
            "квота платного поиска — повтор следующим проходом",
            "адреса на сайте нет",
        ]
    )


async def test_waits_name_why_when_and_whether_again(session: AsyncSession) -> None:
    run = await _done_run(session)
    sites = await _silent_sites(session, run)
    at = datetime.now(UTC) + timedelta(minutes=12)
    await _lead(
        session,
        await _accepted(session, run, "chai-sample.in"),
        "pending@chai-sample.in",
        verify_tries=1,
        verify_again_at=at,
    )
    unknown_at = _ago(days=1)
    await _lead(
        session,
        await _accepted(session, run, "xn--musique-cole-ieb.ca"),
        "contact@xn--musique-cole-ieb.ca",
        verification_status="fixture:unknown",
        verified_at=unknown_at,
    )
    for host in ("orto-sample.it", "vinho-sample.pt"):
        await _lead(
            session,
            await _accepted(session, run, host),
            f"ola@{host}",
            cleaning_note="проверка не выполнена: сеть до провайдера не дошла",
        )
    await _lead(session, await _accepted(session, run, "sauna-sample.fi"), "moi@sauna-sample.fi")  # fmt: skip

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    later_site = await _fresh(session, sites["later"])
    assert later_site.contact_attempted_at is not None
    day = (unknown_at + timedelta(days=7)).strftime("%d.%m.%Y")
    assert view.waits == [
        segment_waits.Wait(
            "address",
            "сайт не ответил поиску адреса",
            1,
            later_site.contact_attempted_at + timedelta(days=1),
        ),
        segment_waits.Wait("cleaning", "Hunter ещё проверяет адрес", 1, at),
        segment_waits.Wait(
            "cleaning",
            f"адрес не подтверждён (unknown) — перепроверка не раньше {day}",
            1,
            again=False,
        ),
        segment_waits.Wait("cleaning", "проверка не выполнена: сеть до провайдера не дошла", 2),
        segment_waits.Wait("cleaning", "ещё не очищали", 1),
    ]


async def test_rare_reasons_go_into_one_line(session: AsyncSession) -> None:
    run = await _done_run(session)
    for n in range(segment_waits.TOP_NOTES + 2):
        host = f"shop-{n}-sample.example.test"
        await _lead(
            session, await _accepted(session, run, host), f"info@{host}",
            cleaning_note=f"ждут: компанию держит непроверенный лид другой гипотезы (лид №{n})",
        )  # fmt: skip

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    assert [wait.count for wait in view.waits] == [1] * segment_waits.TOP_NOTES + [2]
    assert view.waits[-1].words == segment_waits.OTHER_NOTES


# --- маршрут «Повторить сейчас» -------------------------------------------------------------------


async def test_again_queues_the_retry_under_the_run_number_and_journals_it(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await _done_run(session)
    await _silent_sites(session, run)

    response = await client.post(
        f"{RUNS}/{run.id}/again", json={"what": "addresses"}, headers=headers
    )
    second = await client.post(
        f"{RUNS}/{run.id}/again", json={"what": "addresses"}, headers=headers
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"run_id": run.id, "job_id": segment_job_id(run.id)}
    assert (second.status_code, second.json()["detail"]) == (409, segment_api.RUN_GOING)
    [(args, kwargs)] = jobs.enqueued
    assert (args, kwargs["job_id"]) == ((SEGMENT_JOB, run.id, "addresses"), segment_job_id(run.id))
    refreshed = await session.get(type(run), run.id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.job_id, refreshed.finished_at) == (segment_job_id(run.id), None)
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details == {
        "действие": "повтор сбора базы продаж: поиск адреса",
        "прогон": run.id,
        "этап": "sales",
    }


async def test_again_cleaning_queues_its_own_retry(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await _done_run(session)
    await _lead(session, await _accepted(session, run, BAKERY), f"zakaz@{BAKERY}")

    response = await client.post(
        f"{RUNS}/{run.id}/again", json={"what": "cleaning"}, headers=headers
    )

    assert response.status_code == 202, response.text
    [(args, _)] = jobs.enqueued
    assert args == (SEGMENT_JOB, run.id, "cleaning")


@pytest.mark.parametrize(
    ("what", "words"),
    [
        (
            "addresses",
            "Повторять нечего: сайтов, которые ждут повтора поиска адреса, в прогоне нет",
        ),
        ("cleaning", "Повторять нечего: лидов прогона, которых возьмёт очистка, нет"),
    ],
)
async def test_again_with_nothing_to_retry_is_refused_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    what: str,
    words: str,
) -> None:
    run = await _done_run(session)
    await _accepted(session, run, YOGA)  # первый поиск — не повтор

    response = await client.post(f"{RUNS}/{run.id}/again", json={"what": what}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, words)
    assert jobs.enqueued == []


async def test_again_needs_the_run_right(
    session: AsyncSession, client: AsyncClient, make_user: Any, sign_in: Any, jobs: _Jobs
) -> None:
    """Повтор тратит (поиск адреса, проверка) — право «run», как у «Продолжить»."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    await make_user("viewer@ours.example.test", permissions={"run": False})
    viewer = bearer(await sign_in("viewer@ours.example.test"))

    refused = await client.post(
        f"{RUNS}/{run.id}/again", json={"what": "addresses"}, headers=viewer
    )

    assert refused.status_code == 403
    assert jobs.enqueued == []


async def test_again_of_an_unknown_run_or_an_unknown_kind_is_refused(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await _done_run(session)

    unknown = await client.post(f"{RUNS}/987654/again", json={"what": "addresses"}, headers=headers)
    odd = await client.post(f"{RUNS}/{run.id}/again", json={"what": "everything"}, headers=headers)

    assert (unknown.status_code, unknown.json()["detail"]) == (
        404,
        "прогона сбора №987654 нет — обновите список прогонов",
    )
    assert odd.status_code == 422
    assert jobs.enqueued == []


async def test_the_run_report_carries_the_waits(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await _done_run(session)
    sites = await _silent_sites(session, run)

    detail = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()

    later_site = await _fresh(session, sites["later"])
    assert later_site.contact_attempted_at is not None
    term = later_site.contact_attempted_at + timedelta(days=1)
    assert detail["waits"] == [
        {
            "kind": "address",
            "words": "сайт не ответил поиску адреса",
            "count": 1,
            "at": term.isoformat().replace("+00:00", "Z"),
            "again": True,
        }
    ]
    assert detail["waiting_address"] == 3
    count = await session.scalar(select(func.count()).select_from(Candidate))
    assert count == 5
