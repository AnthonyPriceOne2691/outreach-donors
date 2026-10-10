"""Очистка лидов продаж кнопкой — `GET`/`POST /api/sales/clean` и задача очереди продаж.

`GET` перед запуском: сколько лидов гипотезы ждут очистки и платная ли проверка адресов —
окно подтверждения расхода называет это число. `POST` ставит задачу «одна за раз» (образец —
сборка очереди писем): идущая — 409 словами, права — `sales` и `run`, гипотезы нет — 404.
Задача идёт тем же путём, что `outreach sales-clean --hypothesis`: тот же проверяльщик, тот же
проход партиями и тот же журнал расхода. Сеть — заглушки: DNS — таблицей, Hunter —
`httpx.MockTransport`; адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.api.sales import clean as clean_api
from backend.api.sales.clean import SalesCleanBody, SalesCleanView
from backend.cli.main import build_parser
from backend.cli.sales import run_clean
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import ConfigError
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.ops import job_outcome
from backend.features.sales import clean_jobs, cleaning
from backend.features.sales.cleaning import CleaningReport
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.verifier import FixtureVerifier, HunterVerifier
from backend.shared.queue import QUEUE_NAME, SALES_QUEUE_NAME
from fastapi import FastAPI
from httpx import AsyncClient, Response
from rq.exceptions import DuplicateJobError
from rq.job import JobStatus
from rq.results import Result
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
CLEAN = "/api/sales/clean"
ROUTES = {"none-a.example.test": MailRoute.NONE, "none-b.example.test": MailRoute.NONE}
TYPES = (Path(__file__).resolve().parent.parent / "frontend/src/api/salesTypes.ts").read_text(
    encoding="utf-8"
)
#: Ответ Hunter «адрес есть» — по нему считается платная единица.
VALID = {"data": {"status": "valid", "score": 91}}


@pytest.fixture(autouse=True)
def _dns_by_table(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(host: str, **_kwargs: object) -> MailRoute:
        return ROUTES.get(host, MailRoute.MX)

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _hypothesis(session: AsyncSession, name: str = "сайты EN") -> SalesHypothesisModel:
    made = SalesHypothesisModel(name=name)
    session.add(made)
    await session.flush()
    return made


async def _leads(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    *emails: str,
    status: LeadStatus = LeadStatus.NEW,
) -> None:
    """Лиды гипотезы; домен компании — домен адреса, одна строка `domains` на хост."""
    for email in emails:
        host = email.rpartition("@")[2]
        domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
        if domain is None:
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()
        session.add(
            SalesLeadModel(
                hypothesis_id=hypothesis.id,
                domain_id=domain.id,
                email=email,
                source=LeadSource.IMPORT,
                status=status,
            )
        )
    await session.flush()


@dataclass(frozen=True, slots=True)
class _Result:
    """Итог задачи rq: его отчёт — `return_value`."""

    return_value: Any


class _Redis:
    """Redis в том, что нужно постановке и строке задачи: итоги rq 2.12 хранит отдельно от
    задач — ключом `Result.get_key(номер)`, и стирает их только `Result.delete_all`."""

    def __init__(self) -> None:
        self.results: dict[str, _Result] = {}

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.results.pop(key, None)


class _Job:
    """Задача rq, какой её видят постановка и строка задачи: статус, удаление следа и итог —
    по номеру из Redis, а не из самой задачи."""

    def __init__(self, job_id: str, func_name: str, redis: _Redis) -> None:
        self.id = job_id
        self.func_name = func_name
        self.origin = "sales"
        self.connection = redis
        self.status = JobStatus.QUEUED
        self.deleted = False
        self.retries_left = 3
        self.ended_at = None

    def get_status(self, **_options: object) -> JobStatus:
        return self.status

    def latest_result(self) -> _Result | None:
        return self.connection.results.get(Result.get_key(self.id))

    def delete(self) -> None:
        # Как `Job.delete()` rq 2.12: итог задачи остаётся лежать под её номером.
        self.deleted = True

    def finish(self, report: dict[str, Any]) -> None:
        """Воркер дошёл до конца: статус — готово, итог — в Redis под номером задачи."""
        self.status = JobStatus.FINISHED
        self.connection.results[Result.get_key(self.id)] = _Result(report)


class _Jobs:
    """Очередь задач с правилами rq 2.12, что нужны постановке: задача по номеру; при
    `unique=True` занятый номер — `DuplicateJobError`, пока след задачи не удалён."""

    def __init__(self) -> None:
        self.redis = _Redis()
        self.enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.known: dict[str, _Job] = {}

    def fetch_job(self, job_id: str) -> _Job | None:
        found = self.known.get(job_id)
        return None if found is None or found.deleted else found

    def enqueue(self, *args: object, **kwargs: object) -> _Job:
        job_id = str(kwargs["job_id"])
        taken = self.known.get(job_id)
        if kwargs.get("unique") and taken is not None and not taken.deleted:
            raise DuplicateJobError(f"Job with ID '{job_id}' already exists")
        self.enqueued.append((args, kwargs))
        self.known[job_id] = _Job(job_id, str(args[0]), self.redis)
        return self.known[job_id]


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.clean.sales_queue", lambda: found)
    return found


def _screen_reads(jobs: _Jobs, monkeypatch: pytest.MonkeyPatch) -> None:
    """Строка задачи экрана (`GET /api/jobs/{номер}`, `ops/job_outcome`) читает эту очередь, а не
    Redis из настроек. Ею же пользуется сборка очереди продаж (`test_sales_queue_api.py`)."""

    class _Fetch:
        @staticmethod
        def fetch(job_id: str, connection: _Redis) -> _Job:
            assert connection is jobs.redis
            return jobs.known[job_id]

    monkeypatch.setattr(job_outcome, "connection", lambda: jobs.redis)
    monkeypatch.setattr(job_outcome, "Job", _Fetch)


@pytest.fixture
async def waiting(session: AsyncSession) -> SalesHypothesisModel:
    """Гипотеза с двумя лидами, ждущими очистки, и одним уже готовым."""
    hypothesis = await _hypothesis(session)
    await _leads(session, hypothesis, "ivan@acme.example.test", "olga@beta.example.test")
    await _leads(session, hypothesis, "maria@gamma.example.test", status=LeadStatus.READY)
    await session.commit()
    return hypothesis


#: Отчёт прежней очистки — выдуманный, не круглый.
REPORT = {"checked": 7, "ready": 5, "rejected": {"duplicate": 2}, "stopped": None}


async def _start(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> Response:
    return await client.post(CLEAN, json={"hypothesis_id": hypothesis_id}, headers=headers)


# --- перед запуском: сколько ждёт и платно ли -------------------------------------------------


@pytest.mark.parametrize(("provider", "paid"), [("fixture", False), (" Live ", True)])
async def test_screen_learns_how_many_wait_and_whether_the_check_is_paid(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    paid: bool,
) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", provider)
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
    other = await _hypothesis(session, "сайты DE")
    await _leads(session, other, "hans@delta.example.test")
    await session.commit()

    response = await client.get(f"{CLEAN}?hypothesis={waiting.id}", headers=headers)

    assert response.status_code == 200, response.text
    # Ждут — только `new` этой гипотезы: готовый лид и чужая гипотеза не в счёт.
    assert response.json() == {"hypothesis_id": waiting.id, "waiting": 2, "paid": paid}


async def test_waiting_is_exactly_what_the_pass_takes(
    session: AsyncSession, waiting: SalesHypothesisModel
) -> None:
    """Окно называет число тем же условием, каким лидов берёт проход: иначе «до N запросов»
    обещало бы не то, что потратит очистка."""
    found = await clean_jobs.waiting(session, waiting.id)

    report = await cleaning.clean(session, FixtureVerifier(), hypothesis_id=waiting.id)

    assert (found.name, found.count) == ("сайты EN", report.checked)


async def test_unknown_hypothesis_is_404_in_words(
    client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    read = await client.get(f"{CLEAN}?hypothesis=987654", headers=headers)
    start = await _start(client, headers, 987654)

    words = "гипотезы №987654 нет — обновите список гипотез"
    assert (read.status_code, read.json()["detail"]) == (404, words)
    assert (start.status_code, start.json()["detail"]) == (404, words)
    assert jobs.enqueued == []


@pytest.mark.parametrize(
    "body",
    [{"hypothesis_id": 0}, {"hypothesis_id": "семь"}, {}, {"hypothesis_id": 1, "limit": 5}],
)
async def test_body_out_of_the_schema_is_422(
    client: AsyncClient, headers: dict[str, str], jobs: _Jobs, body: dict[str, Any]
) -> None:
    response = await client.post(CLEAN, json=body, headers=headers)

    assert response.status_code == 422
    assert jobs.enqueued == []


# --- запуск задачей --------------------------------------------------------------------------


async def test_clean_goes_to_the_job_queue_and_into_the_journal(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
) -> None:
    response = await _start(client, headers, waiting.id)

    assert response.status_code == 202, response.text
    assert response.json() == {"job_id": clean_api.clean_job_id(waiting.id)}
    [(args, kwargs)] = jobs.enqueued
    assert args == (clean_jobs.CLEAN_JOB, waiting.id)
    # Повторы — как у сборки очереди: временный сбой базы задачу не теряет.
    assert (kwargs["unique"], "retry" in kwargs, "result_ttl" in kwargs) == (True, True, True)
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details == {
        "действие": "очистка лидов продаж",
        "гипотеза": "сайты EN",
        "лидов": 2,
        "проверка адресов": "выдуманная",
    }


def test_clean_goes_to_the_sales_queue_not_the_common_one() -> None:
    """Очистка — в очередь продаж (`worker-sales`): проверка адресов идёт минутами и не держит
    воркер доноров. Очередь — настоящая `rq.Queue`, Redis не трогается."""
    assert clean_api.sales_queue().name == SALES_QUEUE_NAME != QUEUE_NAME


async def test_second_clean_while_the_first_runs_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str], waiting: SalesHypothesisModel, jobs: _Jobs
) -> None:
    """Двойное «Очистить» — одна очистка: вторая проверила бы те же адреса и заплатила дважды."""
    first = await _start(client, headers, waiting.id)
    second = await _start(client, headers, waiting.id)

    assert first.status_code == 202, first.text
    assert (second.status_code, second.json()["detail"]) == (409, clean_api.CLEAN_RUNNING)
    assert len(jobs.enqueued) == 1


@pytest.mark.parametrize("ended", [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELED])
async def test_clean_after_the_previous_one_ended_goes_again(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    ended: JobStatus,
) -> None:
    """След кончившейся задачи лежит неделю: новая очистка гипотезы не ждёт неделю 409."""
    await _start(client, headers, waiting.id)
    previous = jobs.known[clean_api.clean_job_id(waiting.id)]
    previous.status = ended

    again = await _start(client, headers, waiting.id)

    assert again.status_code == 202, again.text
    assert previous.deleted
    assert len(jobs.enqueued) == 2


async def test_new_clean_in_the_queue_does_not_show_the_report_of_the_previous_one(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Итог прежней очистки rq хранит отдельно от задачи, и `Job.delete()` его не трогает: новая
    очистка под тем же номером, пока стоит в очереди, показала бы отчёт прежней. Строка задачи —
    тем же путём, что у экрана: `GET /api/jobs/{номер}`."""
    _screen_reads(jobs, monkeypatch)
    job_id = clean_api.clean_job_id(waiting.id)
    await _start(client, headers, waiting.id)
    jobs.known[job_id].finish(REPORT)
    before = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()

    again = await _start(client, headers, waiting.id)
    after = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()

    assert (before["kind"], before["state"], before["report"]) == (
        "очистка лидов продаж",
        "done",
        REPORT,
    )
    assert again.status_code == 202, again.text
    assert (after["state"], after["report"]) == ("queued", None)


@pytest.mark.parametrize("waits", [JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED])
async def test_clean_waiting_for_a_retry_still_counts_as_running(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    waits: JobStatus,
) -> None:
    await _start(client, headers, waiting.id)
    jobs.known[clean_api.clean_job_id(waiting.id)].status = waits

    second = await _start(client, headers, waiting.id)

    assert second.status_code == 409
    assert len(jobs.enqueued) == 1


async def test_two_clicks_racing_past_the_check_still_clean_once(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Оба нажатия прошли проверку, а поставить успел один — второму `unique=True` даёт 409."""
    monkeypatch.setattr(jobs, "fetch_job", lambda _job_id: None)

    first = await _start(client, headers, waiting.id)
    second = await _start(client, headers, waiting.id)

    assert (first.status_code, second.status_code) == (202, 409)
    assert len(jobs.enqueued) == 1


async def test_nothing_waits_is_refused_in_words_before_the_queue(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    hypothesis = await _hypothesis(session)
    await _leads(session, hypothesis, "maria@gamma.example.test", status=LeadStatus.READY)
    await session.commit()

    response = await _start(client, headers, hypothesis.id)

    assert (response.status_code, response.json()["detail"]) == (409, clean_api.NOTHING_WAITS)
    assert jobs.enqueued == []


async def test_live_without_a_key_is_refused_at_the_button_not_in_the_job(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Негодная настройка проверяльщика — 409 до очереди задач: человек видит причину у
    кнопки, а не в итоге задачи через минуты, и словами — настройки задаёт администратор,
    их имена — в журнале."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")

    with caplog.at_level(logging.WARNING, logger=clean_api.__name__):
        read = await client.get(f"{CLEAN}?hypothesis={waiting.id}", headers=headers)
        start = await _start(client, headers, waiting.id)

    words = "Очистка не запустится: проверка адресов не настроена — настраивает администратор"
    assert (read.status_code, read.json()["detail"]) == (409, words)
    assert (start.status_code, start.json()["detail"]) == (409, words)
    assert jobs.enqueued == []
    journal = [r.__dict__["error"] for r in caplog.records if r.name == clean_api.__name__]
    assert len(journal) == 2
    assert all("CONTACTS_HUNTER_API_KEY пуст" in line for line in journal)


@pytest.mark.parametrize(
    ("taken", "words"),
    [
        ({"sales": False}, "Действие «sales» недоступно этой учётке"),
        # Платная проверка адресов — под `run`, как платный поиск адресов в ядре.
        ({"run": False}, "Действие «run» недоступно этой учётке"),
    ],
)
async def test_without_the_sales_or_the_run_right_the_clean_is_refused_in_words(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    waiting: SalesHypothesisModel,
    jobs: _Jobs,
    taken: dict[str, bool],
    words: str,
) -> None:
    await make_user(SELLER, permissions=taken)

    response = await _start(client, bearer(await sign_in(SELLER)), waiting.id)

    assert (response.status_code, response.json()["detail"]) == (403, words)
    assert jobs.enqueued == []


async def test_reading_needs_only_the_sales_right(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, waiting: SalesHypothesisModel
) -> None:
    """Сколько ждёт — видит и тот, кто запустить не может: экран объяснит, кто запускает."""
    await make_user(SELLER, permissions={"run": False})
    headers = bearer(await sign_in(SELLER))

    allowed = await client.get(f"{CLEAN}?hypothesis={waiting.id}", headers=headers)
    await make_user("other@ours.example.test", permissions={"sales": False})
    refused = await client.get(
        f"{CLEAN}?hypothesis={waiting.id}",
        headers=bearer(await sign_in("other@ours.example.test")),
    )

    assert allowed.status_code == 200
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "Действие «sales» недоступно этой учётке",
    )


async def test_clean_routes_are_these_two(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(CLEAN)
        for method in methods
    }

    assert in_app == {("GET", CLEAN), ("POST", CLEAN)}


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст. Им же
    сверяют поля сборка очереди и гипотеза (`test_sales_queue_api.py`, `test_sales_hypothesis_api.py`)."""
    found = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", TYPES, re.DOTALL)
    assert found is not None, f"в salesTypes.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_screen_reads_the_clean_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки."""
    assert _screen_fields("SalesCleanView") == set(SalesCleanView.model_fields)
    assert _screen_fields("SalesCleanBody") == set(SalesCleanBody.model_fields)
    assert _screen_fields("SalesCleanReport") == set(clean_jobs.report_of(CleaningReport()))


# --- задача ------------------------------------------------------------------------------------


def test_job_is_named_in_words_on_the_screen() -> None:
    """Строка задачи на экране говорит, что это за задача, а не путь функции."""
    assert job_outcome.KINDS[clean_jobs.CLEAN_JOB] == "очистка лидов продаж"


def test_job_settles_a_misconfigured_verifier_as_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор задачи ключ не вставит: итог «не выполнена», а не три попытки."""

    async def refused(_hypothesis_id: int) -> dict[str, Any]:
        raise ConfigError("SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст")

    monkeypatch.setattr(clean_jobs, "run_clean", refused)
    monkeypatch.setattr(clean_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(clean_jobs, "check_storage", lambda: None)

    result = clean_jobs.clean_sales_leads(5)

    # Итог задачи читает человек в строке задачи — словами; имена настроек — в журнале.
    assert result == {
        "error": "проверка адресов не настроена — настраивает администратор",
        "permanent": True,
    }


def test_job_lets_a_passing_failure_go_to_the_queue_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_hypothesis_id: int) -> dict[str, Any]:
        raise ConnectionError("база не ответила")

    monkeypatch.setattr(clean_jobs, "run_clean", broken)
    monkeypatch.setattr(clean_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(clean_jobs, "check_storage", lambda: None)

    with pytest.raises(ConnectionError, match="база не ответила"):
        clean_jobs.clean_sales_leads(5)


async def test_job_cleans_with_its_own_session_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, bool]] = []

    async def clean(
        _session: AsyncSession, http: httpx.AsyncClient, hypothesis_id: int
    ) -> CleaningReport:
        seen.append((hypothesis_id, http.is_closed))
        return CleaningReport(checked=3, ready=2, verifier="fixture")

    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(clean_jobs, "clean_hypothesis", clean)

    result = await clean_jobs.run_clean(5)

    assert seen == [(5, False)]
    assert (result["checked"], result["ready"], result["verifier"]) == (3, 2, "fixture")


async def test_screen_and_console_clean_with_the_same_inputs(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Урок L63: у очистки два пути — задача с кнопки и консоль. Разойдись их входы, лиды
    с кнопки и из консоли чистились бы по-разному под тем же именем."""
    hypothesis = await _hypothesis(session)
    seen: list[dict[str, Any]] = []

    async def clean(_session: AsyncSession, verifier: object, **kwargs: Any) -> CleaningReport:
        seen.append({"проверяльщик": type(verifier), **kwargs})
        return CleaningReport()

    monkeypatch.setattr(cleaning, "clean", clean)
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    args = build_parser().parse_args(["sales-clean", "--hypothesis", "сайты EN"])

    await clean_jobs.run_clean(hypothesis.id)
    await run_clean(session, args)

    assert len(seen) == 2
    assert seen[0] == seen[1] == {"проверяльщик": FixtureVerifier, "hypothesis_id": hypothesis.id}


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"очистка на fixture пошла в сеть: {request.url}")


async def _outcomes(session: AsyncSession, hypothesis: SalesHypothesisModel) -> list[Any]:
    rows = await session.execute(
        select(SalesLeadModel.status, SalesLeadModel.rejection_reason)
        .where(SalesLeadModel.hypothesis_id == hypothesis.id)
        .order_by(SalesLeadModel.id)
    )
    return [tuple(row) for row in rows.tuples()]


async def test_job_takes_the_new_leads_of_its_hypothesis_like_the_console(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Те же данные — тот же итог у задачи и у консоли; чужая гипотеза не тронута; на
    `fixture` — ни одного запроса в сеть."""
    by_job, by_console = await _hypothesis(session, "A"), await _hypothesis(session, "B")
    untouched = await _hypothesis(session, "C")
    for hypothesis, mark in ((by_job, "a"), (by_console, "b")):
        await _leads(
            session,
            hypothesis,
            f"ivan@acme-{mark}.example.test",
            f"ivan@acme-{mark}.example.test",
            f"bounce@acme-{mark}.example.test",
            f"x@none-{mark}.example.test",
        )
    await _leads(session, untouched, "hans@delta.example.test")
    await session.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(_no_network)) as http:
        report = await clean_jobs.clean_hypothesis(session, http, by_job.id)
        args = build_parser().parse_args(["sales-clean", "--hypothesis", "B"])
        await run_clean(session, args, http=http)

    printed = capsys.readouterr().out
    assert (report.checked, report.ready, report.rejected_total) == (4, 1, 3)
    assert f"проверено {report.checked}; готово {report.ready}; отклонено 3" in printed
    assert await _outcomes(session, by_job) == await _outcomes(session, by_console)
    assert await _outcomes(session, untouched) == [(LeadStatus.NEW, None)]


async def _paid_rows(session: AsyncSession) -> list[int | None]:
    rows = await session.scalars(
        select(UsageRecordModel.units)
        .where(UsageRecordModel.operation == cleaning.OPERATION)
        .order_by(UsageRecordModel.id)
    )
    return list(rows)


async def test_live_job_pays_through_the_same_verifier_and_journal_as_the_console(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой проверяльщик у задачи — тот же Hunter общим ключом, и платные проверки ложатся
    в тот же журнал расхода, строкой на партию, что у консоли."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
    asked: list[str] = []

    def hunter(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url.params["email"]))
        return httpx.Response(200, json=VALID)

    by_job, by_console = await _hypothesis(session, "A"), await _hypothesis(session, "B")
    await _leads(session, by_job, "ivan@acme-a.example.test", "x@none-a.example.test")
    await _leads(session, by_console, "ivan@acme-b.example.test", "x@none-b.example.test")
    await session.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(hunter)) as http:
        job = await clean_jobs.clean_hypothesis(session, http, by_job.id)
        after_job = await _paid_rows(session)
        await run_clean(
            session, build_parser().parse_args(["sales-clean", "--hypothesis", "B"]), http=http
        )

    assert (job.verifier, job.paid_units) == (HunterVerifier.name, 1)
    # Домен без почты до платной проверки не дошёл — ни у задачи, ни у консоли.
    assert asked == ["ivan@acme-a.example.test", "ivan@acme-b.example.test"]
    assert after_job == [1]
    assert await _paid_rows(session) == [1, 1]
