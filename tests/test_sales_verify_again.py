"""Перепроверка адреса, у которого Hunter «ещё проверяет» (202), — сама, по сроку и со счётом.

Разбор прогона сбора: после переспросов прохода лид оставался «новым» со словами «повторим
следующей очисткой», и следующей очистки никто не запускал. Здесь: срок и счёт у лида, слова со
временем, задача очереди на срок (`enqueue_at`, своего планировщика нет), тело задачи на
настоящей базе, предел перепроверок — «не проверить» словами, вердикт — в месячный потолок,
202 — нет.

Hunter — `httpx.MockTransport` или выдуманный проверяльщик (`pending@` — «ещё проверяет»), очередь
— подделка с правилами rq 2.12. Адреса и ниши разные, домены выдуманы.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config.startup_checks import ConfigError
from backend.features.contacts.mx import MailRoute
from backend.features.sales import cleaning, deferred, hunter_codes, verify_again, verify_again_jobs
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.verifier import FixtureVerifier, HunterVerifier
from backend.features.sales.verify_cap import MonthCapped
from backend.shared.net import retry
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import DuplicateJobError
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Job, _Redis
from tests.test_sales_cleaning import NOW, World

#: Умолчания настроек: перепроверка через двадцать минут, не больше трёх раз.
TERM = timedelta(minutes=20)
#: Лиды разных ниш, рынков и зон, с доменами IDN в punycode: пекарня (.de), клиника (.com.br),
#: чайная (IDN в .jp), книжная лавка (IDN в .рф), магазин книг (IDN в зоне IDN, .امارات).
PENDING = "pending.bakery@xn--bckerei-beispiel-vnb.de"
CHECKING = "checking-office@clinica-sample.com.br"
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


# --- исход «ещё проверяет»: срок, счёт, слова -----------------------------------------------------


async def test_still_checking_keeps_the_lead_new_with_the_term_the_count_and_words(
    world: World, later: Later
) -> None:
    pending = await world.lead(PENDING)
    fine = await world.lead(FINE)

    report = await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    waiting = await _lead(world.session, pending.id)
    assert (waiting.status, waiting.verify_tries, waiting.verify_again_at) == (
        LeadStatus.NEW,
        1,
        NOW + TERM,
    )
    assert waiting.cleaning_note == (
        "проверка не выполнена: Hunter ещё проверяет адрес — перепроверка сама около 09:50 UTC "
        "(1 из 3)"
    )
    assert waiting.verification_status is None
    ready = await _lead(world.session, fine.id)
    assert (ready.status, ready.verify_tries, ready.verify_again_at) == (LeadStatus.READY, 0, None)
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
    assert (found.verify_tries, found.verify_again_at) == (1, NOW + TERM)
    assert len(later.planned) == 1


async def test_after_the_last_recheck_the_lead_is_refused_in_words(
    world: World, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не больше `SALES_VERIFY_AGAIN_TRIES` перепроверок: следующее «ещё проверяет» — отказ
    «не подтверждён» словами, срок и счёт сняты, задачи на срок больше нет."""
    monkeypatch.setattr("backend.config.sales.VERIFY_AGAIN_TRIES", 2)
    monkeypatch.setattr("backend.config.sales.VERIFY_AGAIN_MIN", 45)
    lead = await world.lead(PENDING)

    for step in range(3):
        await cleaning.clean(world.session, FixtureVerifier(), now=NOW + step * timedelta(hours=1))
        if step < 2:
            assert (await _lead(world.session, lead.id)).verify_tries == step + 1

    found = await _lead(world.session, lead.id)
    assert (found.status, found.rejection_reason) == (
        LeadStatus.REJECTED,
        RejectionReason.UNCONFIRMED.value,
    )
    assert found.cleaning_note == (
        "адрес не проверить: Hunter не закончил проверку и после 2 перепроверок через 45 мин"
    )
    assert (found.verify_tries, found.verify_again_at) == (0, None)
    assert len(later.planned) == 2  # третий проход отказал — нового срока нет


async def test_any_other_outcome_takes_the_term_and_the_count_away(
    world: World, later: Later
) -> None:
    lead = await world.lead(FINE)
    lead.verify_tries, lead.verify_again_at = 2, NOW - timedelta(minutes=1)
    await world.session.flush()

    await cleaning.clean(world.session, FixtureVerifier(), now=NOW)

    found = await _lead(world.session, lead.id)
    assert (found.status, found.verify_tries, found.verify_again_at) == (LeadStatus.READY, 0, None)
    assert later.planned == []


# --- задача на срок: тело на базе ------------------------------------------------------------------


async def test_the_recheck_takes_only_the_leads_whose_term_came(world: World, later: Later) -> None:
    """Срок настал (и в пределах минуты) — перепроверка; ещё не настал, отклонён или без срока —
    не трогает. Перепроверенный «ещё проверяет» — следующий срок и счёт +1."""
    due = await world.lead(PENDING)
    soon = await world.lead(FINE)
    future = await world.lead(OTHER)
    gone = await world.lead("checking@gone-sample.co.uk", status=LeadStatus.REJECTED)
    plain = await world.lead(BOOKS)
    due.verify_tries, due.verify_again_at = 1, NOW - timedelta(minutes=5)
    soon.verify_tries, soon.verify_again_at = 1, NOW + timedelta(seconds=40)
    future.verify_tries, future.verify_again_at = 1, NOW + timedelta(minutes=9)
    gone.verify_tries, gone.verify_again_at = 1, NOW - timedelta(minutes=5)
    await world.session.commit()

    report = await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW)

    assert report is not None
    assert report.checked == 2
    rows = {
        lead.id: (lead.status, lead.verify_tries, lead.verify_again_at)
        for lead in (await world.session.scalars(select(SalesLeadModel))).all()
    }
    assert rows[due.id] == (LeadStatus.NEW, 2, NOW + TERM)
    assert rows[soon.id] == (LeadStatus.READY, 0, None)
    assert rows[future.id] == (LeadStatus.NEW, 1, NOW + timedelta(minutes=9))
    assert rows[gone.id][0] is LeadStatus.REJECTED
    assert rows[plain.id] == (LeadStatus.NEW, 0, None)


async def test_the_recheck_with_nothing_due_is_empty(world: World) -> None:
    await world.lead(FINE)
    await world.session.commit()

    assert await verify_again_jobs.again_pass(world.session, FixtureVerifier(), now=NOW) is None


async def test_the_next_term_is_the_earliest_left_in_the_base(world: World, later: Later) -> None:
    """Следующая задача — на самый ранний срок в базе, не ближе минуты от «сейчас»; минута, у
    которой задача уже есть, второй не получает."""
    first = await world.lead(PENDING)
    second = await world.lead(OTHER)
    first.verify_tries, first.verify_again_at = 1, NOW + timedelta(minutes=7)
    second.verify_tries, second.verify_again_at = 1, NOW - timedelta(minutes=3)
    await world.session.flush()

    planned = await verify_again.plan(world.session, now=NOW)
    again = await verify_again.plan(world.session, now=NOW)

    assert planned == "sales-verify-again-20261004T0931"
    assert again is None
    assert [(when, number) for when, _, _, number in later.planned] == [
        (NOW + deferred.SOONEST, "sales-verify-again-20261004T0931")
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
    """Потолок месяца выбран — перепроверка не спрашивает Hunter и встаёт его словами: лид ждёт
    в прежнем статусе, срок снят (до следующего месяца перепроверять бессмысленно)."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "0")
    lead = await world.lead(FINE)
    lead.verify_tries, lead.verify_again_at = 1, NOW - timedelta(minutes=2)
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
    assert (found.status, found.verify_again_at) == (LeadStatus.NEW, None)


# --- задача очереди: отказы как у очистки ---------------------------------------------------------


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
