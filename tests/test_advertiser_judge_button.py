"""«Переобойти спорные» кнопкой: смета, запуск по ней, ход и итог.

Проверяется то, ради чего кнопка так устроена: смета называет кого и сколько до траты и берёт только
спорных; запуск не возьмёт больше, чем в смете, которую видел человек, и пишется в журнал; выбранный
потолок и пустая смета — отказ словами, а не задача; второй разбор не ставится; без права `run` — нет;
экран отличает ход, итог, остановку с причиной и обход, умерший вместе с задачей. Модель и сайты —
подставные, очередь — подставная с правилами rq 2.12 (`tests/test_letters_one_job.py`).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.advertisers import judge_pass as api
from backend.config import llm as llm_cfg
from backend.features.core.domain import AuditAction, UserRole, Verdict
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.core.usage import record
from backend.features.crawl import judge_estimate, judge_pass
from backend.features.crawl.judge_pass import (
    JUDGE_OPERATION,
    RESTARTING,
    PassReport,
    plan_pass,
    report_lines,
    run_pass,
)
from backend.features.crawl.judge_store import cohort_of
from backend.features.ops.job_outcome import KINDS, JobOutcome, advertisers_job
from backend.shared.llm import ModelUnreadyError
from backend.shared.numbers import spaced
from backend.shared.queue import (
    CRAWL_QUEUE_NAME,
    JUDGE_DISPUTED_JOB,
    JUDGE_DISPUTED_JOB_ID,
    JUDGE_DISPUTED_TIMEOUT,
    RESULT_TTL,
)
from backend.workers import judge_jobs
from httpx import AsyncClient
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_advertiser_judge import Model, client_of, facts_answer
from tests.test_advertiser_judge_pass import DONOR, Site, _candidates, _pending
from tests.test_letters_one_job import _Jobs

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/advertisers/judge-pass"
AT = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def model_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка модели до траты — подставная: настоящая пошла бы к провайдеру."""

    async def ready(*_models: str) -> None:
        return None

    monkeypatch.setattr(judge_pass, "ensure_model", ready)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Подставная очередь обходов: задача стоит, пока её номер известен; обходчик один и жив."""
    found = _Jobs()

    def outcome(job_id: str) -> JobOutcome | None:
        return JobOutcome(job_id, "разбор", "queued") if job_id in found.known else None

    def meta(job_id: str) -> dict[str, Any]:
        return dict(found.enqueued[-1][1]["meta"]) if job_id in found.known else {}

    monkeypatch.setattr(api, "crawl_queue", lambda: found)
    monkeypatch.setattr(api, "workers_alive", lambda **_: 1)
    monkeypatch.setattr(api, "job_alive", lambda job_id: (job_id in found.known) or None)
    monkeypatch.setattr(api, "job_outcome", outcome)
    monkeypatch.setattr(api, "job_meta", meta)
    return found


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("оператор@site.example.test", role=UserRole.OPERATOR)
    return bearer(await sign_in("оператор@site.example.test"))


async def _judged(session: AsyncSession, target: str) -> None:
    """Пара уже рассужена нынешней когортой: план её не берёт."""
    cohort = cohort_of(llm_cfg.ADVERTISER_JUDGE_MODEL)
    session.add(
        AdvertiserJudgementModel(
            donor_host=DONOR,
            target_root=target,
            context_print="отпечаток",
            prompt_version=cohort.prompt_version,
            prompt_print=cohort.prompt_print,
            examples_print=cohort.examples_print,
            model=cohort.model,
            rules_version="правила",
            facts={},
            advice="unclear",
            rule="правило",
            seen="текст",
            tokens=4000,
            updated_at=datetime.now(UTC),
        )
    )
    await session.flush()


# --- смета ----------------------------------------------------------------------------------------


async def test_the_estimate_takes_only_the_disputed_and_names_what_it_skips(
    session: AsyncSession,
) -> None:
    await _candidates(session, *_pending("new1.example", "new2.example", "done.example"))
    await _candidates(session, ("bought.example", Verdict.BOUGHT, 5))
    await _candidates(session, *_pending("nopage.example"), page=False)
    await _judged(session, "done.example")

    found = await judge_estimate.estimate(session)

    assert found.cases == 2
    assert found.tokens == 2 * 4000
    assert found.refusal is None
    words = "\n".join(found.lines)
    assert "Спорных к разбору: 2, доноров: 1" in words
    assert "Уже разобраны, страницы не открываем: 1" in words
    assert "Без страницы со ссылкой, разбирать нечего: 1" in words
    assert f"по {spaced(4000)} на случай" in words


async def test_the_estimate_says_how_many_the_daily_cap_will_reach(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 10_000)
    await _judged(session, "done.example")
    await _candidates(session, *_pending(*(f"shop{n}.example" for n in range(5))))
    record(session, operation=JUDGE_OPERATION, units=2_000)
    await session.flush()

    found = await judge_estimate.estimate(session)

    room = next(line for line in found.lines if line.startswith("Потолок судьи"))
    assert f"осталось {spaced(8000)} из {spaced(10_000)}" in room
    assert "хватит примерно на 2, остальных разберёт следующий запуск" in room


async def test_a_spent_cap_refuses_in_words_before_the_button(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 100)
    await _candidates(session, *_pending("new1.example"))
    record(session, operation=JUDGE_OPERATION, units=150)
    await session.flush()

    found = await judge_estimate.estimate(session)

    assert found.refusal is not None
    assert "судьи рекламодателей" in found.refusal
    # Смета названа и с отказом: человек видит, что ждёт разбора.
    assert found.cases == 1


# --- запуск ---------------------------------------------------------------------------------------


async def test_the_start_takes_no_more_than_the_seen_estimate_and_goes_to_the_journal(
    client: AsyncClient, session: AsyncSession, operator: dict[str, str], jobs: _Jobs
) -> None:
    await _candidates(session, *_pending("new1.example", "new2.example", "new3.example"))
    await session.commit()

    answer = await client.post(URL, json={"cases": 2}, headers=operator)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["state"] == "queued"
    assert (body["total"], body["by"]) == (2, "оператор@site.example.test")
    ((args, options),) = jobs.enqueued
    assert args == (JUDGE_DISPUTED_JOB, 2)
    assert options["job_id"] == JUDGE_DISPUTED_JOB_ID
    assert options["job_timeout"] == JUDGE_DISPUTED_TIMEOUT
    assert options["result_ttl"] == RESULT_TTL
    # Нажал человек, а за ним могут стоять тысячи обходов.
    assert options["at_front"] is True
    log = (
        await session.scalars(
            select(AuditLogModel).where(AuditLogModel.target == "advertisers:judge-pass")
        )
    ).one()
    assert log.action == AuditAction.ADVERTISER_REVIEWED
    assert log.details["случаев"] == 2


async def test_a_larger_number_than_the_estimate_is_cut_to_it(
    client: AsyncClient, session: AsyncSession, operator: dict[str, str], jobs: _Jobs
) -> None:
    await _candidates(session, *_pending("new1.example"))
    await session.commit()

    answer = await client.post(URL, json={"cases": 500}, headers=operator)

    assert answer.status_code == 200, answer.text
    assert jobs.enqueued[0][0] == (JUDGE_DISPUTED_JOB, 1)


async def test_a_second_start_is_refused_while_the_first_goes(
    client: AsyncClient, session: AsyncSession, operator: dict[str, str], jobs: _Jobs
) -> None:
    await _candidates(session, *_pending("new1.example"))
    await session.commit()
    await client.post(URL, json={"cases": 1}, headers=operator)

    answer = await client.post(URL, json={"cases": 1}, headers=operator)

    assert answer.status_code == 409
    assert answer.json()["detail"] == api.ALREADY_RUNNING
    assert len(jobs.enqueued) == 1


async def test_a_job_whose_crawler_died_does_not_hold_the_next_start(
    client: AsyncClient,
    session: AsyncSession,
    operator: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Статус «идёт» rq снимет не сразу: без уборки кнопка отвечала бы «уже идёт» о мёртвой задаче."""
    await _candidates(session, *_pending("new1.example"))
    await session.commit()
    await client.post(URL, json={"cases": 1}, headers=operator)
    jobs.known[JUDGE_DISPUTED_JOB_ID].status = JobStatus.STARTED
    monkeypatch.setattr(api, "job_alive", lambda _id: False)

    answer = await client.post(URL, json={"cases": 1}, headers=operator)

    assert answer.status_code == 200, answer.text
    assert len(jobs.enqueued) == 2


async def test_nothing_to_judge_and_a_spent_cap_set_no_job(
    client: AsyncClient,
    session: AsyncSession,
    operator: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = await client.post(URL, json={"cases": 1}, headers=operator)
    assert (empty.status_code, empty.json()["detail"]) == (409, api.NOTHING_TO_DO)

    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 100)
    await _candidates(session, *_pending("new1.example"))
    record(session, operation=JUDGE_OPERATION, units=150)
    await session.commit()
    capped = await client.post(URL, json={"cases": 1}, headers=operator)
    assert capped.status_code == 409
    assert "судьи рекламодателей" in capped.json()["detail"]

    assert jobs.enqueued == []


async def test_without_the_run_right_only_the_estimate_is_visible(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    jobs: _Jobs,
) -> None:
    await make_user("смотрит@site.example.test", role=UserRole.OPERATOR, permissions={"run": False})
    headers = bearer(await sign_in("смотрит@site.example.test"))
    await _candidates(session, *_pending("new1.example"))
    await session.commit()

    seen = await client.get(URL, headers=headers)

    assert seen.status_code == 200, seen.text
    assert seen.json()["estimate"]["cases"] == 1
    assert seen.json()["state"] == "idle"
    assert (await client.post(URL, json={"cases": 1}, headers=headers)).status_code == 403
    assert jobs.enqueued == []


async def test_a_refused_section_sees_nothing(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, jobs: _Jobs
) -> None:
    """Раздел закрыт правом «Рекламодатели» на роутере (`stage_access.STAGE_TWO`)."""
    await make_user(
        "продажи@site.example.test", role=UserRole.OPERATOR, permissions={"advertisers": False}
    )
    headers = bearer(await sign_in("продажи@site.example.test"))

    assert (await client.get(URL, headers=headers)).status_code == 403
    assert (await client.post(URL, json={"cases": 1}, headers=headers)).status_code == 403
    assert jobs.enqueued == []


async def test_no_crawlers_refuse_in_words(
    client: AsyncClient,
    session: AsyncSession,
    operator: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "workers_alive", lambda **_: 0)
    await _candidates(session, *_pending("new1.example"))
    await session.commit()

    seen = (await client.get(URL, headers=operator)).json()

    assert seen["estimate"]["refusal"] == api.NO_WORKERS
    assert jobs.enqueued == []


# --- ход и итог -----------------------------------------------------------------------------------


def _seen(
    state: str,
    *,
    report: dict[str, Any] | None = None,
    error: str | None = None,
    alive: bool | None = True,
    meta: dict[str, Any] | None = None,
) -> api._Seen:
    outcome = JobOutcome(
        job_id=JUDGE_DISPUTED_JOB_ID,
        kind="разбор спорных судьёй",
        state=state,
        report=report,
        error=error,
        ended_at=AT,
    )
    return api._Seen(outcome, alive, meta or {"by": "кто", "done": 40, "total": 90}, 1)


def test_no_job_is_idle() -> None:
    assert api._job_state(api._Seen(None, False, {}, 1)) == {"state": "idle"}


def test_a_running_job_shows_its_progress() -> None:
    state = api._job_state(_seen("running"))
    assert (state["state"], state["done"], state["total"], state["by"]) == (
        "running",
        40,
        90,
        "кто",
    )


def test_a_job_whose_crawler_died_is_a_break_and_not_running_forever() -> None:
    state = api._job_state(_seen("running", alive=False))
    assert (state["state"], state["why"]) == ("failed", api.WORKER_DIED)


def test_an_unanswered_queue_is_not_taken_for_a_dead_job() -> None:
    state = api._job_state(_seen("running", alive=None))
    assert state["state"] == "running"
    unknown = api._job_state(_seen("unknown", error="что с задачей, сейчас не узнать"))
    assert unknown == {"state": "unknown", "why": "что с задачей, сейчас не узнать"}


def test_the_end_gives_the_lines_of_the_report() -> None:
    report = {"done": 90, "total": 90, "stopped": "", "lines": ["рассужены: 90", "токенов: 9 000"]}
    state = api._job_state(_seen("done", report=report))
    assert state["state"] == "done"
    assert (state["done"], state["total"]) == (90, 90)
    assert state["lines"] == ["рассужены: 90", "токенов: 9 000"]
    assert state["why"] is None
    assert state["ended_at"] == AT
    # Итог, записанный до чисел советов: советы — только в строках, а не «советов нет».
    assert state["advice"] is None


def test_the_end_gives_the_advice_by_numbers_for_the_short_line() -> None:
    """Разведка экранов 10.11.2026: итог стоял двумя строками мелкого текста — на виду советы
    числами, строки — в «i»."""
    report = {"done": 23, "total": 23, "stopped": "", "lines": ["рассужены: 23"]}
    counted = {**report, "advice": {"write": 13, "skip": 5, "unclear": 5}}

    state = api._job_state(_seen("done", report=counted))

    assert state["advice"] == {"write": 13, "skip": 5, "unclear": 5}
    assert state["lines"] == ["рассужены: 23"]


def test_a_stop_keeps_its_reason() -> None:
    report = {"done": 40, "total": 90, "stopped": "дневной потолок выбран", "lines": []}
    state = api._job_state(_seen("done", report=report))
    assert (state["state"], state["why"], state["done"]) == (
        "stopped",
        "дневной потолок выбран",
        40,
    )


@pytest.mark.parametrize("kind", ["failed", "refused"])
def test_a_fall_and_a_refusal_keep_their_reason(kind: str) -> None:
    state = api._job_state(_seen(kind, error="у ключа нет кредитов"))
    assert (state["state"], state["why"]) == ("failed", "у ключа нет кредитов")


# --- задача --------------------------------------------------------------------------------------


def test_the_job_path_resolves_and_has_words_and_a_section() -> None:
    module, _, name = JUDGE_DISPUTED_JOB.rpartition(".")
    assert getattr(importlib.import_module(module), name) is judge_jobs.judge_disputed
    assert KINDS[JUDGE_DISPUTED_JOB] == "разбор спорных судьёй"
    assert advertisers_job(JUDGE_DISPUTED_JOB_ID, JUDGE_DISPUTED_JOB, CRAWL_QUEUE_NAME, (5,))


class _Engine:
    """Движок задачи: её сессии — в транзакции теста, закрывать ей нечего."""

    async def dispose(self) -> None:
        return None


async def test_the_job_judges_no_more_than_confirmed_and_leaves_the_decision(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тело задачи: не больше подтверждённых, в итоге — слова и токены, решение человека на месте."""
    ids = await _candidates(session, *_pending("new1.example", "new2.example", "new3.example"))
    first = await session.get(CandidateModel, ids[0])
    assert first is not None
    first.confirmed = True
    await session.commit()
    model = Model(facts_answer())
    site = Site()
    monkeypatch.setattr(judge_jobs, "create_async_engine", lambda *_a, **_k: _Engine())
    monkeypatch.setattr(
        judge_jobs,
        "async_sessionmaker",
        lambda *_a, **_k: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(judge_jobs, "guarded_client", lambda **_: site.client())
    monkeypatch.setattr(judge_jobs, "JudgeClient", lambda _http: client_of(model))
    monkeypatch.setattr(judge_pass.crawl_cfg, "DELAY_SEC", 0)

    report = await judge_jobs._pass(2)

    assert (report["total"], report["done"], report["left"]) == (2, 2, 0)
    assert report["stopped"] == ""
    assert report["tokens"] > 0
    assert report["lines"][0] == "рассужены: 2"
    assert report["lines"][-1].startswith("токенов: ")
    # Советы числами — тем же счётом, что строка «советы: …».
    assert sum(report["advice"].values()) == 2
    assert set(report["advice"]) <= {"write", "skip", "unclear"}
    session.expire_all()
    rows = await session.scalars(select(AdvertiserJudgementModel))
    assert len(rows.all()) == 2
    kept = await session.get(CandidateModel, ids[0])
    assert kept is not None
    assert (kept.confirmed, kept.verdict) == (True, Verdict.PENDING)


def test_an_unready_model_is_a_reason_and_not_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пополнить счёт может только человек: причина — итогом задачи, экран скажет «не выполнена»."""

    async def unready(_cases: int) -> dict[str, Any]:
        raise ModelUnreadyError("у ключа модели нет кредитов")

    monkeypatch.setattr(judge_jobs, "_pass", unready)
    monkeypatch.setattr(judge_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(judge_jobs, "check_storage", lambda: None)

    report = judge_jobs.judge_disputed(5)

    assert report["permanent"] is True
    assert "нет кредитов" in report["error"]


async def test_a_stop_request_ends_the_pass_at_the_chunk_border(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge_pass, "CHUNK", 2)
    await _candidates(session, *_pending(*(f"shop{n}.example" for n in range(5))))
    model = Model(facts_answer())
    site = Site()
    asks: list[int] = []

    def stop() -> bool:
        asks.append(1)
        return len(asks) > 1

    async with site.client() as http:
        report = await run_pass(
            session,
            await plan_pass(session),
            client=client_of(model),
            http=http,
            delay=0,
            stop=stop,
        )

    assert (report.done, report.left) == (2, 3)
    assert report.stopped == RESTARTING
    assert len(model.requests) == 2


def test_the_report_words_are_shared_by_the_screen_and_the_console() -> None:
    report = PassReport()
    report.outcomes.update({"judged": 3, "blind": 1})
    report.advice.update({"write": 2, "unclear": 1})
    report.cached = 1

    lines = report_lines(report)

    assert lines[0] == "рассужены: 3"
    assert any(line.startswith("советы: ") and "пишем 2" in line for line in lines)
    assert "из кэша, без модели: 1" in lines
