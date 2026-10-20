"""Все сайты прогона гипотезы и пересуд — маршруты `/api/sales/segment/runs/{N}/sites` и
`/rejudge` на настоящей базе.

Список: каждая полоса и «ещё не судили», кто решил (правило, модель, человек — с автором и
временем), итог каждого вида тем же правилом, что числа отчёта, страницы без молчаливой обрезки,
«было» и «стало бы» после пересуда. Пересуд: смета, запуск задачей очереди долгих задач продаж,
отказ словами, пока идёт прогон или пересуд, описание после сметы — заново.

Очередь задач — подделка с правилами rq (`test_sales_clean_api._Jobs`), живость задач — таблицей;
домены выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.errors import QUEUE_DOWN
from backend.api.sales import segment_sites as sites_api
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, ContactStatus, Permission
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.ops.job_outcome import JobOutcome
from backend.features.sales import segment_rejudge_estimate as se
from backend.features.sales import segment_report, segment_watch
from backend.features.sales.models import LeadStatus, RejectionReason, SalesHypothesisModel
from backend.features.sales.segment_jobs import segment_job_id
from backend.features.sales.segment_judge import segment_digest
from backend.features.sales.segment_models import Decider, Lane, SalesSegmentRunModel, Stage
from backend.features.sales.segment_rejudge import REJUDGE_JOB, rejudge_job_id
from backend.features.sales.segment_rejudge_report import DONE, GOING, KEY, QUEUED, write_block
from backend.features.sales.segment_sites import (
    FOUND_NO_LEAD,
    NO_ADDRESS_WORDS,
    NOT_SEARCHED,
    PAGE_SIZE,
    address_words,
)
from httpx import AsyncClient
from redis.exceptions import RedisError
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_rejudge import (
    MAYBE,
    NEWS,
    PARTS,
    SHOP,
    lead_of,
    report,
    site,
)
from tests.test_sales_segment_run import SEGMENT, hypothesis, make_run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
RUNS = "/api/sales/segment/runs"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def owner(session: AsyncSession) -> SalesHypothesisModel:
    return await hypothesis(session)


@pytest.fixture
async def run(session: AsyncSession, owner: SalesHypothesisModel) -> SalesSegmentRunModel:
    return await make_run(session, owner, mode=JudgeMode.ENFORCE)


@pytest.fixture(autouse=True)
def alive(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool | None]:
    """Живость задач очереди — таблицей: номер задачи → жива ли (нет в таблице — нет задачи).
    Соединение очереди, если его передали, — не в счёт: таблица и есть очередь. Та же таблица —
    у уборки мёртвых задач (`segment_watch.drop_dead_job`)."""
    table: dict[str, bool | None] = {}

    def alive_of(job_id: str, *_args: object, **_kwargs: object) -> bool | None:
        return table.get(job_id, False)

    monkeypatch.setattr(sites_api, "job_alive", alive_of)
    monkeypatch.setattr(segment_watch, "job_alive", alive_of)
    return table


@pytest.fixture(autouse=True)
def outcomes(monkeypatch: pytest.MonkeyPatch) -> dict[str, JobOutcome]:
    """Исход задач в очереди — таблицей: номер задачи → исход (нет в таблице — очередь не знает)."""
    table: dict[str, JobOutcome] = {}
    monkeypatch.setattr(sites_api, "job_outcome", table.get)
    return table


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr(sites_api, "sales_bulk_queue", lambda: found)
    return found


@pytest.fixture(autouse=True)
def spendable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модуль включён, ключ модели есть, потолка нет: смета не отказывает по настройкам."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(llm_cfg, "API_KEY", MODEL_KEY)

    async def no_cap(_session: AsyncSession, _now: datetime | None = None) -> int | None:
        return None

    monkeypatch.setattr(se, "tokens_left", no_cap)


async def every_lane(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, int]:
    """По сайту на каждый вид: принятые с лидом, с формой, без адреса, не искали; на проверку —
    моделью и без ответа модели; отказ судьи в наблюдении («судья отказал бы»); отсечённые
    правилом, моделью и человеком; ещё не судили."""
    made = {
        "lead": await site(session, run, SHOP, lane=Lane.ACCEPT),
        "form": await site(session, run, "form-only.example.test", lane=Lane.ACCEPT,
                           contact_status=ContactStatus.FORM_ONLY),
        "none": await site(session, run, "no-address.example.test", lane=Lane.ACCEPT,
                           contact_status=ContactStatus.NOT_FOUND),
        "silent": await site(session, run, "silent.example.test", lane=Lane.ACCEPT,
                             contact_status=ContactStatus.NO_ANSWER),
        "fresh": await site(session, run, PARTS, lane=Lane.ACCEPT),
        "review": await site(session, run, MAYBE, lane=Lane.REVIEW, verdict=Lane.REVIEW),
        "unanswered": await site(session, run, "quiet.example.test", lane=Lane.REVIEW,
                                 verdict=None, decided_by=None, unanswered=True,
                                 reason="модель не ответила: 503"),
        "shadow": await site(session, run, "masterskie-katalog.example.test",
                             lane=Lane.WOULD_REJECT, verdict=Lane.REJECT,
                             reason="каталог мастерских — сам не чинит",
                             quote="Все веломастерские города в одном каталоге"),
        "rule": await site(session, run, "youtube.com", lane=Lane.REJECT, decided_by=Decider.RULE,
                           verdict=Lane.REJECT, reason="платформам и соцсетям не пишем"),
        "model": await site(session, run, NEWS, lane=Lane.REJECT, verdict=Lane.REJECT,
                            reason="пишет о чужих", quote="Новости велоспорта и обзоры"),
        "human": await site(session, run, "human.example.test", lane=Lane.REJECT,
                            verdict=Lane.ACCEPT, decided_by=Decider.HUMAN, reason="отказал человек",
                            decided_by_user=SELLER,
                            decided_at=datetime(2026, 10, 14, 9, 31, tzinfo=UTC)),
        "pending": await site(session, run, "waiting.example.test", lane=Lane.PENDING),
    }  # fmt: skip
    lead = await lead_of(session, made["lead"])
    lead.status, lead.rejection_reason = LeadStatus.REJECTED, RejectionReason.DUPLICATE
    await session.commit()
    return {name: candidate.id for name, candidate in made.items()}


# --- список ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lane", "names"),
    [
        ("accept", {"lead", "form", "none", "silent", "fresh"}),
        ("review", {"review", "unanswered"}),
        ("would_reject", {"shadow"}),
        ("reject", {"rule", "model", "human"}),
        ("pending", {"pending"}),
        ("all", None),
    ],
)
async def test_each_lane_lists_its_sites_and_the_tabs_count_all_of_them(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    lane: str,
    names: set[str] | None,
) -> None:
    ids = await every_lane(session, run)

    response = await client.get(f"{RUNS}/{run.id}/sites?lane={lane}", headers=headers)

    assert response.status_code == 200, response.text
    found = response.json()
    wanted = set(ids) if names is None else names
    assert {row["id"] for row in found["rows"]} == {ids[name] for name in wanted}
    assert found["total"] == len(wanted)
    assert found["lanes"] == {
        "all": 12,
        "accept": 5,
        "review": 2,
        "would_reject": 1,
        "reject": 3,
        "pending": 1,
        "changed": 0,
    }


async def test_who_decided_is_named_and_filtered_and_a_human_comes_with_the_author(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], run: SalesSegmentRunModel
) -> None:
    ids = await every_lane(session, run)

    response = await client.get(f"{RUNS}/{run.id}/sites?lane=reject&decider=human", headers=headers)

    found = response.json()
    [human] = found["rows"]
    assert human["id"] == ids["human"]
    assert (human["decided_by"], human["decided_by_user"]) == ("human", SELLER)
    assert human["decided_at"] == "2026-10-14T09:31:00Z"
    assert (human["verdict"], human["reason"]) == ("accept", "отказал человек")
    # «язык» — вердикт языка сайта (`Decider.LANGUAGE`): в этом прогоне таких нет.
    assert found["deciders"] == {"rule": 1, "model": 1, "human": 1, "language": 0, "none": 0}


async def test_the_outcome_of_each_kind_and_the_lead_link(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], run: SalesSegmentRunModel
) -> None:
    ids = await every_lane(session, run)

    response = await client.get(f"{RUNS}/{run.id}/sites", headers=headers)

    rows = {row["id"]: row for row in response.json()["rows"]}
    lead = rows[ids["lead"]]["outcome"]
    assert (lead["kind"], lead["lead_status"], lead["lead_reason"]) == (
        "lead",
        "rejected",
        "duplicate",
    )
    assert lead["lead_id"] is not None
    assert rows[ids["form"]]["outcome"] == {
        "kind": "form_only",
        "words": "только форма на сайте",
        "lead_id": None,
        "lead_status": None,
        "lead_reason": None,
    }
    assert rows[ids["none"]]["outcome"]["kind"] == "no_address"
    assert rows[ids["silent"]]["outcome"]["kind"] == "other"
    assert rows[ids["fresh"]]["outcome"]["words"] == NOT_SEARCHED
    for name in ("review", "shadow", "rule", "model", "human", "pending"):
        assert rows[ids[name]]["outcome"] is None, name


async def test_the_outcome_words_are_the_rule_of_the_run_report(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Одно правило у итога сайта и у чисел «без адреса — почему» отчёта прогона: вызов, а не
    копия (`segment_report._addresses` зовёт `segment_sites.address_words`)."""
    for status in ContactStatus:
        await site(session, run, f"{status.value.replace('_', '-')}.example.test", lane=Lane.ACCEPT,
                   contact_status=status)  # fmt: skip

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    assert sorted(words for words, _ in view.no_address) == sorted(
        address_words(status) for status in ContactStatus
    )
    assert address_words(ContactStatus.FOUND) == FOUND_NO_LEAD
    assert set(NO_ADDRESS_WORDS.values()) <= {words for words, _ in view.no_address}


async def test_pages_are_counted_on_the_server_and_nothing_is_cut_silently(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], run: SalesSegmentRunModel
) -> None:
    hosts = [f"site-{n:03d}.example.test" for n in range(PAGE_SIZE + 3)]
    for host in hosts:
        await site(session, run, host, lane=Lane.ACCEPT)

    first = (await client.get(f"{RUNS}/{run.id}/sites", headers=headers)).json()
    second = (await client.get(f"{RUNS}/{run.id}/sites?page=2", headers=headers)).json()
    past = (await client.get(f"{RUNS}/{run.id}/sites?page=9", headers=headers)).json()

    assert (first["total"], second["total"], past["total"]) == (len(hosts),) * 3
    assert (len(first["rows"]), len(second["rows"]), past["rows"]) == (PAGE_SIZE, 3, [])
    assert (first["limit"], first["page"], second["page"]) == (PAGE_SIZE, 1, 2)
    listed = [row["host"] for row in first["rows"] + second["rows"]]
    assert listed == hosts, "порядок полный, ни один сайт не пропал и не задвоился"


async def test_after_a_rejudge_the_list_shows_what_was_and_what_would_be(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], run: SalesSegmentRunModel
) -> None:
    moved = await site(session, run, PARTS, lane=Lane.REJECT)
    kept = await site(session, run, SHOP, lane=Lane.ACCEPT)
    await lead_of(session, kept)
    same = await site(session, run, MAYBE, lane=Lane.REVIEW)
    block = {
        "state": DONE,
        "sites": {
            str(moved.id): ["accept", "reject"],
            str(kept.id): ["accept", "reject"],
            str(same.id): ["review", "review"],
        },
        "would": {str(kept.id): {"lane": "reject", "reason": "не тот сегмент", "quote": "x y z"}},
    }
    await write_block(session, run.id, block)
    await session.commit()

    found = (await client.get(f"{RUNS}/{run.id}/sites?lane=changed", headers=headers)).json()

    rows = {row["id"]: row for row in found["rows"]}
    assert set(rows) == {moved.id, kept.id}
    assert found["lanes"]["changed"] == 2
    assert rows[moved.id]["change"] == {
        "was": "accept",
        "now": "reject",
        "would": False,
        "reason": None,
        "quote": None,
    }
    assert rows[kept.id]["lane"] == "accept"
    assert rows[kept.id]["change"]["would"] is True
    assert rows[kept.id]["change"]["reason"] == "не тот сегмент"


async def test_sites_of_an_unknown_run_are_404_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(f"{RUNS}/987654/sites", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "прогона сбора №987654 нет — обновите список прогонов",
    )


async def test_the_list_needs_the_sales_right(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    run: SalesSegmentRunModel,
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(f"{RUNS}/{run.id}/sites", headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, no_right(Permission.SALES))


# --- пересуд: смета и запуск ----------------------------------------------------------------------------


async def test_the_rejudge_estimate_comes_before_the_run(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await site(session, run, PARTS, lane=Lane.ACCEPT)

    response = await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)

    assert response.status_code == 200, response.text
    found = response.json()
    assert (found["estimate"]["sites"], found["estimate"]["model"]) == (2, 2)
    # И один вызов разбора описания: у прогона его ещё нет.
    assert found["estimate"]["tokens"] == 2 * se.TOKENS_PER_SITE + se.SCOPE_TOKENS
    assert found["estimate"]["refusal"] is None
    assert (found["last"], found["going"]) == (None, False)


async def _start(client: AsyncClient, headers: dict[str, str], run_id: int, digest: str) -> object:
    return await client.post(f"{RUNS}/{run_id}/rejudge", json={"digest": digest}, headers=headers)


async def test_the_rejudge_is_queued_marked_in_the_report_and_journaled(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    digest = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()["estimate"][
        "digest"
    ]

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": digest}, headers=headers
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"run_id": run.id, "job_id": rejudge_job_id(run.id)}
    [(args, options)] = jobs.enqueued
    assert args == (REJUDGE_JOB, run.id)
    assert options["job_id"] == rejudge_job_id(run.id)
    block = (await report(session, run))[KEY]
    assert (block["state"], block["by"]) == (QUEUED, SELLER)
    entry = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert entry is not None
    assert entry.details["действие"] == "пересуд сбора базы продаж"


@pytest.mark.parametrize(
    ("job", "words"),
    [
        ("run", sites_api.RUN_GOING),
        ("rejudge", sites_api.REJUDGE_GOING),
    ],
)
async def test_the_rejudge_is_refused_in_words_while_the_run_or_a_rejudge_goes(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
    job: str,
    words: str,
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    alive[segment_job_id(run.id) if job == "run" else rejudge_job_id(run.id)] = True

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (409, words)
    assert jobs.enqueued == []


def _digest() -> str:
    return segment_digest(SEGMENT)


async def test_a_silent_queue_is_not_taken_for_an_idle_run(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    alive[segment_job_id(run.id)] = None

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (503, sites_api.QUEUE_SILENT)
    assert jobs.enqueued == []


async def test_a_description_edited_after_the_estimate_needs_a_new_estimate(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    owner: SalesHypothesisModel,
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    seen = _digest()
    owner.description = "Другой сегмент: прокат велосипедов."
    await session.commit()

    response = await client.post(f"{RUNS}/{run.id}/rejudge", json={"digest": seen}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, sites_api.DIGEST_STALE)
    assert jobs.enqueued == []


async def test_an_estimate_refusal_closes_the_start(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    await site(session, run, SHOP, lane=Lane.PENDING)

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == f"Пересуд не запущен: {se.NOTHING}"


async def test_a_queue_failure_puts_the_previous_rejudge_back(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    earlier = {"state": DONE, "sites": {}, "tokens": 377}
    await write_block(session, run.id, earlier)
    await session.commit()

    class Down(_Jobs):
        """Очередь ответила, что задачи пересуда нет, а поставить не смогла."""

        def enqueue(self, *_args: object, **_kwargs: object) -> Any:
            raise RedisError("очередь недоступна")

    monkeypatch.setattr(sites_api, "sales_bulk_queue", Down)

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert response.status_code == 503
    assert (await report(session, run))[KEY] == earlier


async def test_the_rejudge_needs_the_run_right(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    run: SalesSegmentRunModel,
    jobs: _Jobs,
) -> None:
    await make_user(SELLER, permissions={"run": False})

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge",
        json={"digest": _digest()},
        headers=bearer(await sign_in(SELLER)),
    )

    assert (response.status_code, response.json()["detail"]) == (403, no_right(Permission.RUN))
    assert jobs.enqueued == []


async def test_the_course_of_a_going_rejudge_and_a_lost_one(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
) -> None:
    """Ход — «пересуд: X из Y» из отчёта прогона; задача пропала (воркер умер) — экран видит,
    что она не идёт, хотя отчёт ещё говорит «идёт»."""
    block = {"state": GOING, "sites": {"1": ["accept", "reject"], "2": ["review", None]}}
    await write_block(session, run.id, block)
    await session.commit()
    alive[rejudge_job_id(run.id)] = True

    going = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()
    alive[rejudge_job_id(run.id)] = False
    lost = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()

    assert (going["last"]["done"], going["last"]["total"], going["going"]) == (1, 2, True)
    assert (lost["last"]["state"], lost["going"]) == (GOING, False)


async def test_a_rejudge_waiting_for_a_retry_names_the_reason_and_the_time(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    outcomes: dict[str, JobOutcome],
) -> None:
    """Задача упала на временном сбое и ждёт повтора: экран видит причину и время, а не
    «пересуд: 0 из 0»."""
    await write_block(session, run.id, {"state": QUEUED, "sites": {}})
    await session.commit()
    alive[rejudge_job_id(run.id)] = True
    outcomes[rejudge_job_id(run.id)] = JobOutcome(
        job_id=rejudge_job_id(run.id),
        kind="задача",
        state="retry_wait",
        error="база не ответила",
        next_try_at=datetime(2026, 10, 14, 10, 2, tzinfo=UTC),
    )

    found = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()

    assert found["job"] == {
        "state": "retry_wait",
        "error": "база не ответила",
        "next_try_at": "2026-10-14T10:02:00Z",
    }
    assert (found["last"]["state"], found["going"]) == (QUEUED, True)


# --- продолжение прогона и пересуд — не разом ---------------------------------------------------


@pytest.fixture
def run_jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Очередь, куда встаёт продолжение прогона (`api/sales/segment.py`), — подделка с правилами rq."""
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.segment.sales_bulk_queue", lambda: found)
    return found


def rejudging(jobs: _Jobs, run_id: int) -> None:
    """Задача пересуда прогона — в очереди, как её ставит маршрут запуска пересуда."""
    jobs.enqueue(REJUDGE_JOB, run_id, job_id=rejudge_job_id(run_id), unique=True)


def run_passes(jobs: _Jobs) -> list[object]:
    """Поставленные проходы прогона — без задач пересуда."""
    return [
        kwargs["job_id"] for _, kwargs in jobs.enqueued if "rejudge" not in str(kwargs["job_id"])
    ]


async def test_resume_while_the_rejudge_goes_is_refused_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    run_jobs: _Jobs,
) -> None:
    """«Продолжить» и «Найти адреса принятым» посреди пересуда: проход судил бы те же сайты, а его
    запись отчёта прогона затёрла бы ход пересуда — отказ словами, проход не ставится."""
    rejudging(run_jobs, run.id)
    alive[rejudge_job_id(run.id)] = True

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, sites_api.RESUME_REJUDGING)
    assert run_passes(run_jobs) == []
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is None


async def test_resume_is_not_queued_when_the_queue_is_silent_about_the_rejudge(
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    run_jobs: _Jobs,
) -> None:
    """Очередь не сказала, идёт ли пересуд: неизвестность — не «не идёт»."""
    rejudging(run_jobs, run.id)
    alive[rejudge_job_id(run.id)] = None

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (503, sites_api.RESUME_SILENT)
    assert run_passes(run_jobs) == []


async def test_resume_with_the_queue_down_is_the_common_queue_refusal(
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    run_jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis лежит целиком — отказ теми же словами, что у постановки продолжения
    (`api/errors.QUEUE_DOWN`), а не тишина и не «пересуда нет»."""

    def down(_job_id: str) -> None:
        raise RedisError("очередь не ответила")

    monkeypatch.setattr(run_jobs, "fetch_job", down)

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (503, QUEUE_DOWN)
    assert run_passes(run_jobs) == []


async def test_resume_after_the_rejudge_is_over_queues_the_run(
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    run_jobs: _Jobs,
) -> None:
    """Пересуд кончился (задачи нет) или идёт пересуд другого прогона — продолжение ставится."""
    rejudging(run_jobs, run.id + 1)
    alive[rejudge_job_id(run.id + 1)] = True

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert response.status_code == 202, response.text
    assert run_passes(run_jobs) == [segment_job_id(run.id)]


async def test_resume_goes_when_the_rejudge_job_is_left_by_a_dead_worker(
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    run_jobs: _Jobs,
) -> None:
    """Задача пересуда «идёт», а её воркер умер (`queue.job_alive` — нет): пересуд не идёт, и
    продолжение прогона не ждёт таймаута очереди."""
    rejudging(run_jobs, run.id)
    alive[rejudge_job_id(run.id)] = False

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert response.status_code == 202, response.text
    assert run_passes(run_jobs) == [segment_job_id(run.id)]


async def test_a_rejudge_is_refused_while_a_resumed_pass_of_a_finished_run_goes(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    """Обратное: готовый прогон продолжили («Найти адреса принятым») — стадия у него прежняя,
    «готово», а проход идёт; пересуд смотрит на задачу прохода, а не на стадию."""
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    run.stage = Stage.DONE
    await session.commit()
    alive[segment_job_id(run.id)] = True
    found = (await client.get(f"{RUNS}/{run.id}/rejudge", headers=headers)).json()

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", headers=headers, json={"digest": found["estimate"]["digest"]}
    )

    assert (response.status_code, response.json()["detail"]) == (409, sites_api.RUN_GOING)
    assert jobs.enqueued == []


# --- воркер умер посреди пересуда ---------------------------------------------------------------


async def cut_short(
    session: AsyncSession, run: SalesSegmentRunModel, jobs: _Jobs, digest: str
) -> dict[str, Any]:
    """Пересуд шёл и оборвался: воркер умер посреди (память, перезапуск контейнера). Блок —
    «идёт», половина сайтов пересужена; задача rq висит «идёт», а её воркера нет."""
    shop = await site(session, run, SHOP, lane=Lane.ACCEPT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    block = {
        "state": GOING,
        "job_id": rejudge_job_id(run.id),
        "by": SELLER,
        "digest": digest,
        "sites": {str(shop.id): ["reject", "accept"], str(parts.id): ["accept", None]},
        "tokens": 1500,
    }
    await write_block(session, run.id, block)
    await session.commit()
    jobs.enqueue(REJUDGE_JOB, run.id, job_id=rejudge_job_id(run.id), unique=True)
    jobs.known[rejudge_job_id(run.id)].status = JobStatus.STARTED
    return block


async def test_a_rejudge_cut_short_by_a_dead_worker_goes_on_from_its_snapshot(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    """«Оборвался» → «Пересудить»: мёртвая задача убрана, а прерванный пересуд того же описания
    продолжается со своего снимка — блок не переписан: «было» у пересуженных сайтов цело, к модели
    они второй раз не идут, второй пересуд в сумму не считается."""
    block = await cut_short(session, run, jobs, _digest())
    dead = jobs.known[rejudge_job_id(run.id)]
    alive[rejudge_job_id(run.id)] = False

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert response.status_code == 202, response.text
    assert dead.deleted
    assert len(jobs.enqueued) == 2
    found = (await report(session, run))[KEY]
    assert {key: found[key] for key in block} == block  # снимок, отпечаток и числа — прежние
    assert found["resumed_by"] == SELLER
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details["действие"] == "продолжение пересуда сбора базы продаж"


async def test_a_rejudge_cut_short_under_another_description_starts_anew(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    """Описание поправили после обрыва — вопрос другой: новый пересуд со своим снимком."""
    await cut_short(session, run, jobs, "другое-описание")
    dead = jobs.known[rejudge_job_id(run.id)]
    alive[rejudge_job_id(run.id)] = False

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert response.status_code == 202, response.text
    assert dead.deleted
    found = (await report(session, run))[KEY]
    assert (found["state"], "sites" in found) == (QUEUED, False)


async def test_a_rejudge_whose_worker_lives_is_refused_and_left_alone(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    run: SalesSegmentRunModel,
    alive: dict[str, bool | None],
    jobs: _Jobs,
) -> None:
    """Задача «идёт» у живого воркера — пересуд и правда идёт: отказ, как был; задача и блок —
    на месте."""
    block = await cut_short(session, run, jobs, _digest())
    alive[rejudge_job_id(run.id)] = True

    response = await client.post(
        f"{RUNS}/{run.id}/rejudge", json={"digest": _digest()}, headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (409, sites_api.REJUDGE_GOING)
    assert not jobs.known[rejudge_job_id(run.id)].deleted
    assert len(jobs.enqueued) == 1
    assert (await report(session, run))[KEY] == block
