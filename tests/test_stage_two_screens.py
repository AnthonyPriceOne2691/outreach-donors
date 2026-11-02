"""Данные Этапа 2 не утекают через общие экраны — решение 02.11.2026.

Маршруты этапа закрыты (`test_stage_two_access.py`), но его строки живут и на общих экранах: числа
«Обзора» и меню, письма в словах отказа у карточки донора, стоп-лист, ящики, исход задач, расход,
тревоги сторожа, журнал. Правило то же, что у продаж (`test_stage_access_ops.py`): без права
«Рекламодатели» этапа нет ни в списках, ни в числах, а с правом всё как было.

Каждое правило — с двух сторон. Роль «Только доноры» здесь — учётка, которая видит только Этап 1;
экраны под правами, которых роли не положено (ящики, журнал), смотрит админ со снятым правом.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.letters import once
from backend.config import crawl as crawl_cfg
from backend.features.access.journal import JournalQuery, advertisers_entry, read_journal
from backend.features.access.repository import AccessRepository
from backend.features.contacts import forms
from backend.features.core import usage
from backend.features.core.domain import (
    AuditAction,
    ContactStatus,
    CrawlOutcome,
    CrawlStatus,
    ReplyKind,
    RunStatus,
    Stage,
    StopReason,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    MessageModel,
    ReplyModel,
    SendingDomainModel,
)
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.crawl import review as advertiser_review
from backend.features.donors.verdict import Thresholds
from backend.features.letters import other_role
from backend.features.ops import job_outcome as outcome_module
from backend.features.ops import silence
from backend.features.ops.job_outcome import DRAFT_JOB as DRAFT_PATH
from backend.features.ops.job_outcome import JobOutcome, advertisers_job, job_outcome
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import SpendingRepository
from backend.shared.queue import (
    BUILD_JOB,
    CONTACTS_JOB,
    CRAWL_JOB,
    CRAWL_QUEUE_NAME,
    JUDGE_CRAWL_JOB,
    LEAD_JOB,
    PARSE_JOB,
    QUEUE_NAME,
    RUN_JOB,
    SEND_QUEUE_JOB,
    crawl_job_id,
    parse_job_id,
)
from backend.workers.agent_jobs import DRAFT_JOB, draft_job_id
from fastapi import FastAPI
from httpx import AsyncClient
from rq.job import Job, JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.stage2_rows import promoted
from tests.test_sales_stage_mail import sender
from tests.test_stage_access_ops import providers_quiet
from tests.test_stage_two_access import World, world

__all__ = ["providers_quiet", "world"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

CLOSED = "— только с правом «Рекламодатели»"
ONLY_DONORS = frozenset({Stage.DONORS})
#: Этапы учётки без права «Рекламодатели», но с «Продажами».
NO_STAGE_TWO = frozenset({Stage.DONORS, Stage.SALES})


@pytest.fixture
async def donors_only(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Роль «Только доноры» с отправкой, выданной поимённо."""
    await make_user("donors@team.example.test", role=UserRole.DONORS, permissions={"send": True})
    return bearer(await sign_in("donors@team.example.test"))


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("operator@team.example.test", permissions={"send": True})
    return bearer(await sign_in("operator@team.example.test"))


@pytest.fixture
async def admin_out(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Админ, у которого право снято: экраны ящиков и журнала — под правами админа."""
    await make_user(
        "admin-out@team.example.test", role=UserRole.ADMIN, permissions={"advertisers": False}
    )
    return bearer(await sign_in("admin-out@team.example.test"))


@pytest.fixture
async def admin_in(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin-in@team.example.test", role=UserRole.ADMIN)
    return bearer(await sign_in("admin-in@team.example.test"))


# --- списки и числа -------------------------------------------------------------------------


async def test_threads_leave_stage_two_out(
    client: AsyncClient, world: World, donors_only: dict[str, str], operator: dict[str, str]
) -> None:
    hidden = await client.get("/api/threads", headers=donors_only)
    shown = await client.get("/api/threads", headers=operator)

    assert [row["id"] for row in hidden.json()] == [world.donor_thread]
    assert {row["id"] for row in shown.json()} == {world.donor_thread, world.thread}


async def test_drafts_and_agent_settings_leave_stage_two_out(
    client: AsyncClient, world: World, donors_only: dict[str, str], operator: dict[str, str]
) -> None:
    hidden = await client.get("/api/agent/drafts", headers=donors_only)
    shown = await client.get("/api/agent/drafts", headers=operator)
    hidden_agent = await client.get("/api/agent/settings", headers=donors_only)
    shown_agent = await client.get("/api/agent/settings", headers=operator)

    assert hidden.json() == []
    assert [draft["id"] for draft in shown.json()] == [world.draft]
    assert [one["stage"] for one in hidden_agent.json()["stages"]] == ["donors"]
    assert [one["stage"] for one in shown_agent.json()["stages"]] == ["donors", "advertisers"]


@pytest.fixture
def stage_two_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Этап 2 ждёт человека: четыре спорных, три бизнеса ниши, две формы рекламодателей."""

    async def counted(number: int, _session: AsyncSession) -> int:
        return number

    def returns(number: int) -> Callable[[AsyncSession], Awaitable[int]]:
        return lambda session: counted(number, session)

    monkeypatch.setattr(advertiser_review, "waiting", returns(4))
    monkeypatch.setattr(niche, "waiting", returns(3))
    monkeypatch.setattr(forms, "advertiser_total", returns(2))


@pytest.mark.usefixtures("stage_two_work")
async def test_the_overview_and_the_menu_count_stage_two_only_with_the_right(
    client: AsyncClient, world: World, donors_only: dict[str, str], operator: dict[str, str]
) -> None:
    """Плитки, письма и числа меню: без права этапа в сводке писем нет вовсе, а не нулями; ответ
    рекламодателя — лид — у «Лидов» и у пункта «Диалоги с рекламодателями» только с правом."""
    hidden = (await client.get("/api/overview", headers=donors_only)).json()
    shown = (await client.get("/api/overview", headers=operator)).json()
    hidden_menu = (await client.get("/api/overview/work", headers=donors_only)).json()
    shown_menu = (await client.get("/api/overview/work", headers=operator)).json()

    assert set(hidden["letters"]) == {"donors"}
    assert (shown["letters"]["advertisers"]["queued"], shown["letters"]["advertisers"]["sent"]) == (
        1,
        1,
    )
    assert hidden["letters"]["donors"] == shown["letters"]["donors"]
    waiting = ("leads", "advertisers", "niche", "advertiser_forms")
    assert [hidden["waiting"][name] for name in waiting] == [0, 0, 0, 0]
    assert [shown["waiting"][name] for name in waiting] == [1, 7, 3, 2]
    assert (hidden["waiting"]["forms"], shown["waiting"]["forms"]) == (0, 2)
    assert (hidden_menu["advertisers"], shown_menu["advertisers"]) == (0, 7)
    assert (hidden_menu["advertiser_threads"], shown_menu["advertiser_threads"]) == (0, 1)
    assert (hidden_menu["forms"], shown_menu["forms"]) == (0, 2)


@pytest.mark.usefixtures("stage_two_work")
async def test_the_forms_screen_does_not_name_the_neighbour_queue(
    client: AsyncClient, donors_only: dict[str, str], operator: dict[str, str]
) -> None:
    hidden = await client.get("/api/contacts/forms", headers=donors_only)
    shown = await client.get("/api/contacts/forms", headers=operator)

    assert hidden.json()["advertisers_waiting"] == 0
    assert shown.json()["advertisers_waiting"] == 2


async def test_stop_list_shows_stage_two_only_with_the_right(
    client: AsyncClient,
    session: AsyncSession,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    """Запись без этапа держит и доноров — её видят все; запись этапа снимают только с правом."""
    everyone = SuppressionModel(
        email="all@site.example.test", reason=SuppressionReason.UNSUBSCRIBED
    )
    stage_two = SuppressionModel(
        email="brand@site.example.test",
        reason=SuppressionReason.MANUAL,
        stage=Stage.ADVERTISERS,
    )
    session.add_all([everyone, stage_two])
    await session.commit()
    path = f"/api/suppressions/{stage_two.id}/remove"

    hidden = (await client.get("/api/suppressions", headers=donors_only)).json()
    shown = (await client.get("/api/suppressions", headers=operator)).json()
    refused = await client.post(path, json={"reason": "вернулся"}, headers=donors_only)
    removed = await client.post(path, json={"reason": "вернулся"}, headers=operator)

    assert [row["id"] for row in hidden["rows"]] == [everyone.id]
    assert hidden["total"] == 1
    assert {row["id"] for row in shown["rows"]} == {everyone.id, stage_two.id}
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"Записи стоп-листа рекламодателей {CLOSED}"
    assert removed.status_code == 200, removed.text


async def test_the_sending_screen_shows_stage_two_boxes_only_with_the_right(
    client: AsyncClient,
    session: AsyncSession,
    admin_out: dict[str, str],
    admin_in: dict[str, str],
) -> None:
    donor = await sender(session, "anna@mail-donors.example.test", Stage.DONORS)
    offers = await sender(session, "offers@mail-offers.example.test", Stage.ADVERTISERS)
    session.add(
        SendingDomainModel(
            domain="mail-offers.example.test", stage=Stage.ADVERTISERS, daily_limit=40
        )
    )
    await session.commit()
    path = f"/api/senders/{offers.id}/disable"

    hidden = (await client.get("/api/senders", headers=admin_out)).json()
    shown = (await client.get("/api/senders", headers=admin_in)).json()
    refused = await client.post(path, json={"reason": "проверка"}, headers=admin_out)

    assert [box["id"] for box in hidden["senders"]] == [donor.id]
    assert hidden["domains"] == []
    assert hidden["enabled_domains"] == 1
    assert [one["stage"] for one in hidden["directions"]] == ["donors", "sales"]
    assert {box["id"] for box in shown["senders"]} == {donor.id, offers.id}
    assert [one["stage"] for one in shown["directions"]] == ["donors", "advertisers", "sales"]
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"Ящики рекламодателей {CLOSED}"


async def test_unbound_replies_to_a_stage_two_box_are_hidden(
    client: AsyncClient,
    session: AsyncSession,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    await sender(session, "offers@mail-offers.example.test", Stage.ADVERTISERS)
    reply = ReplyModel(
        kind=ReplyKind.HUMAN,
        raw_body="What is this about?",
        from_email="someone@site.example.test",
        to_addresses=["offers@mail-offers.example.test"],
    )
    session.add(reply)
    await session.commit()

    hidden = (await client.get("/api/replies/unbound", headers=donors_only)).json()
    shown = (await client.get("/api/replies/unbound", headers=operator)).json()
    refused = await client.get(f"/api/replies/{reply.id}/attachments/1", headers=donors_only)

    assert (hidden["total"], hidden["rows"]) == (0, [])
    assert shown["total"] == 1
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"Ответы в переписках рекламодателей {CLOSED}"


async def test_a_run_card_does_not_carry_the_niche_numbers(
    client: AsyncClient, session: AsyncSession, donors_only: dict[str, str]
) -> None:
    """Бизнесы ниши из выдачи — кандидаты Этапа 2: их число лежит в отчёте прогона и остаётся
    там, а в карточку прогона, которую видит роль, не идёт."""
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.DONE,
        keywords=["garden tools"],
        country="de",
        stats={"niche": {"found": 3, "added": 2}, "judge": {"accepted": 1}},
    )
    session.add(run)
    await session.commit()

    card = (await client.get(f"/api/runs/{run.id}", headers=donors_only)).json()

    assert card["stats"] == {"judge": {"accepted": 1}}
    assert run.stats is not None
    assert run.stats["niche"] == {"found": 3, "added": 2}


# --- письма Этапа 2 в словах об адресате --------------------------------------------------


HIDDEN_WORDS = "уже писали в другой роли"


async def test_the_donor_card_does_not_name_a_stage_two_letter(
    client: AsyncClient,
    session: AsyncSession,
    world: World,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    """Сайту ушло письмо рекламодателю: причина на карточке — без роли и номера письма."""
    letter = await session.get(MessageModel, world.sent)
    assert letter is not None
    domain = await make_donor(session, "both.example.test", email="ceo@both.example.test")
    letter.domain_id = domain.id
    await session.commit()
    donor = (await client.get("/api/donors?limit=50", headers=operator)).json()
    number = next(row["id"] for row in donor["rows"] if row["host"] == "both.example.test")

    hidden = (await client.get(f"/api/donors/{number}", headers=donors_only)).json()
    shown = (await client.get(f"/api/donors/{number}", headers=operator)).json()

    assert HIDDEN_WORDS in hidden["letter_blocked"]
    assert str(letter.id) not in hidden["letter_blocked"]
    assert f"как рекламодателю (письмо №{letter.id})" in shown["letter_blocked"]


def test_the_default_seen_stages_open_nothing() -> None:
    """Забытый довод ничего не открывает: без него видны только письма доноров."""
    written = other_role.Written(id=7, stage=Stage.ADVERTISERS)

    assert HIDDEN_WORDS in other_role.words(written, seen=other_role.ONLY_DONORS)
    assert "как рекламодателю" in other_role.words(written, seen=EVERY_STAGE)


# --- исход задач ---------------------------------------------------------------------------

STAGE_TWO_JOBS = [
    pytest.param(crawl_job_id(5), CRAWL_JOB, CRAWL_QUEUE_NAME, (5,), id="обход"),
    pytest.param("judge-5", JUDGE_CRAWL_JOB, CRAWL_QUEUE_NAME, (5,), id="суд обхода"),
    pytest.param("crawl-elsewhere", "path.of.a.job", CRAWL_QUEUE_NAME, (), id="очередь обходов"),
    pytest.param("lead-7", LEAD_JOB, QUEUE_NAME, (7, "lead-7"), id="передача лида"),
    pytest.param(
        once.build_job_id(Stage.ADVERTISERS, "links"), BUILD_JOB, QUEUE_NAME, (), id="сборка писем"
    ),
    pytest.param(
        once.send_job_id(Stage.ADVERTISERS, "niche"), SEND_QUEUE_JOB, QUEUE_NAME, (), id="пачка"
    ),
    pytest.param(
        "contacts-9",
        CONTACTS_JOB,
        QUEUE_NAME,
        (100, False, False, None, Stage.ADVERTISERS.value),
        id="поиск адресов рекламодателям",
    ),
]
STAGE_ONE_JOBS = [
    pytest.param("run-42", RUN_JOB, QUEUE_NAME, (42,), id="прогон"),
    pytest.param(
        once.build_job_id(Stage.DONORS, "links"), BUILD_JOB, QUEUE_NAME, (), id="сборка доноров"
    ),
    pytest.param("contacts-1", CONTACTS_JOB, QUEUE_NAME, (100, False, False), id="поиск донорам"),
    pytest.param(parse_job_id(7), PARSE_JOB, QUEUE_NAME, (7,), id="разбор цены"),
    pytest.param(draft_job_id(8), DRAFT_JOB, QUEUE_NAME, (8,), id="черновик"),
]


@pytest.mark.parametrize(("job_id", "path", "queue", "args"), STAGE_TWO_JOBS)
def test_a_stage_two_job_is_known_by_what_it_is(
    job_id: str, path: str, queue: str, args: tuple[Any, ...]
) -> None:
    assert advertisers_job(job_id, path, queue, args)


@pytest.mark.parametrize(("job_id", "path", "queue", "args"), STAGE_ONE_JOBS)
def test_other_jobs_are_not_stage_two(
    job_id: str, path: str, queue: str, args: tuple[Any, ...]
) -> None:
    assert not advertisers_job(job_id, path, queue, args)


def test_the_draft_job_path_is_the_workers_one() -> None:
    """Задачу черновика сторож знает путём строкой: без импорта воркера. Связь держит тест."""
    assert DRAFT_PATH == DRAFT_JOB


@pytest.mark.parametrize(
    ("job_id", "path", "args", "stage", "reply"),
    [
        (crawl_job_id(5), CRAWL_JOB, (5,), Stage.ADVERTISERS, None),
        ("run-42", RUN_JOB, (42,), None, None),
        (parse_job_id(7), PARSE_JOB, (7,), None, 7),
        (draft_job_id(8), DRAFT_JOB, (8,), None, 8),
    ],
    ids=["обход", "прогон", "разбор ответа", "черновик ответа"],
)
def test_the_outcome_carries_its_stage_or_its_reply(
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
    path: str,
    args: tuple[Any, ...],
    stage: Stage | None,
    reply: int | None,
) -> None:
    """По настоящей задаче rq. Разбор и черновик этапа не называют: он у ответа."""
    conn: Any = object()
    origin = CRAWL_QUEUE_NAME if path == CRAWL_JOB else QUEUE_NAME
    job = Job.create(path, args=args, connection=conn, id=job_id, origin=origin)
    monkeypatch.setattr(job, "get_status", lambda refresh=True: JobStatus.FINISHED)
    monkeypatch.setattr(job, "latest_result", lambda: None)
    monkeypatch.setattr(outcome_module.Job, "fetch", lambda _job_id, connection: job)

    found = job_outcome(job_id, redis=conn)

    assert found is not None
    assert (found.stage, found.reply_id) == (stage, reply)


async def test_a_stage_two_job_outcome_is_read_only_with_the_right(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    world: World,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    """И задача над ответом рекламодателя: этап у неё — по самому ответу."""
    known = {
        "crawl-5": JobOutcome(
            job_id="crawl-5",
            kind="обход",
            state="done",
            report={"pages": 40},
            stage=Stage.ADVERTISERS,
        ),
        "draft-reply": JobOutcome(
            job_id="draft-reply", kind="черновик ответа", state="done", reply_id=world.reply
        ),
        # Этап задачи сильнее её ответа: черновик в очереди продаж, а ответа уже нет.
        "draft-gone": JobOutcome(
            job_id="draft-gone",
            kind="черновик ответа",
            state="done",
            stage=Stage.SALES,
            reply_id=999_999,
        ),
        "run-42": JobOutcome(job_id="run-42", kind="прогон", state="running"),
    }
    monkeypatch.setattr("backend.api.jobs.routes.job_outcome", known.get)

    gone = await client.get("/api/jobs/draft-gone", headers=donors_only)
    refused = await client.get("/api/jobs/crawl-5", headers=donors_only)
    read = await client.get("/api/jobs/crawl-5", headers=operator)
    of_reply = await client.get("/api/jobs/draft-reply", headers=donors_only)
    of_reply_read = await client.get("/api/jobs/draft-reply", headers=operator)
    run = await client.get("/api/jobs/run-42", headers=donors_only)

    assert gone.status_code == 403, gone.text
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"Задачи рекламодателей {CLOSED}"
    assert read.status_code == 200, read.text
    assert of_reply.status_code == 403, of_reply.text
    assert of_reply_read.status_code == 200, of_reply_read.text
    assert run.status_code == 200, run.text


# --- расход ---------------------------------------------------------------------------------


@pytest.fixture
async def spent(session: AsyncSession) -> None:
    """Прогон доноров, DR кандидатов и письма Этапа 2, платный обход и отправка, общая на всех."""
    usage.record(session, operation="serp", units=500)
    usage.record(session, operation="batch_metrics", units=1000, stage=Stage.DONORS)
    usage.record(session, operation="batch_metrics", units=300, stage=Stage.ADVERTISERS)
    usage.record(session, operation="letter_rewrite", units=70, stage=Stage.ADVERTISERS)
    usage.record(session, operation="crawl_proxy", units=2_000_000, amount_usd=1.5)
    usage.record(session, operation="crawl_render", units=20, amount_usd=0.5)
    usage.record(session, operation="letter_send", units=3)
    await session.commit()


@pytest.mark.usefixtures("spent", "providers_quiet")
async def test_spend_leaves_stage_two_out_of_lines_totals_and_the_crawl_cap(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    """Статьи, суммы и итог сходятся, потолок обхода не назван, что счёт не весь — признаком."""
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 50.0)

    hidden = (await client.get("/api/usage", headers=donors_only)).json()
    shown = (await client.get("/api/usage", headers=operator)).json()

    assert {(line["operation"], line["stage"]) for line in hidden["articles"]} == {
        ("serp", "donors"),
        ("batch_metrics", "donors"),
        ("letter_send", None),
    }
    assert hidden["total_units"] == sum(line["units"] for line in hidden["articles"]) == 1503
    assert [one["stage"] for one in hidden["stages"]] == ["donors", None]
    assert hidden["advertisers_hidden"] is True
    assert hidden["sales_hidden"] is True
    assert hidden["crawl"] == {"cap_usd": None, "spent_usd": "0"}
    # Hunter: учётка общая и с поиском адресов доноров, потолок проверок — расход продаж.
    assert hidden["hunter"]["month"] is None
    assert shown["hunter"]["month"] is not None
    assert shown["advertisers_hidden"] is False
    assert "advertisers" in [one["stage"] for one in shown["stages"]]
    assert float(shown["crawl"]["spent_usd"]) == 2.0
    assert float(shown["crawl"]["cap_usd"]) == 50.0


async def test_spend_keeps_a_row_without_a_stage_when_two_stages_are_hidden(
    session: AsyncSession,
) -> None:
    """Условие над пустым этапом — не пустота: общая отправка письма не пропадает молча."""
    usage.record(session, operation="letter_send", units=1)
    usage.record(session, operation="batch_metrics", units=1, stage=Stage.ADVERTISERS)
    usage.record(session, operation="sales_verify", units=1)
    await session.flush()

    found = await SpendingRepository(session).since_month_start(stages=ONLY_DONORS)

    assert [article.operation for article in found.articles] == ["letter_send"]


# --- сторож ---------------------------------------------------------------------------------


async def _blocked_series(session: AsyncSession) -> None:
    """Три обхода подряд упёрлись в защиту: серия, тревога `crawl-blocked`."""
    for number in range(3):
        session.add(
            CrawlRunModel(
                host=f"blocked-{number}.example.test",
                status=CrawlStatus.DONE,
                outcome=CrawlOutcome.BLOCKED,
                stop_reason=StopReason.NO_START,
                pages_opened=0,
                articles=0,
            )
        )
    await session.commit()


@pytest.fixture
def nobody_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answered() -> None:
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", answered)


@pytest.mark.usefixtures("nobody_down")
async def test_the_watchdog_shows_crawl_alarms_only_with_the_right(
    client: AsyncClient,
    session: AsyncSession,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    await _blocked_series(session)

    hidden = (await client.get("/api/watchdog", headers=donors_only)).json()["alarms"]
    shown = (await client.get("/api/watchdog", headers=operator)).json()["alarms"]

    assert "crawl-blocked" not in {one["code"] for one in hidden}
    assert ("crawl-blocked", "advertisers") in {(one["code"], one["stage"]) for one in shown}


async def test_crawl_alarms_are_counted_only_for_who_sees_the_stage(
    session: AsyncSession,
) -> None:
    await _blocked_series(session)

    hidden = await silence.alarms(session, stages=NO_STAGE_TWO)
    background = await silence.alarms(session, stages=EVERY_STAGE)

    assert "crawl-blocked" not in {alarm.code for alarm in hidden}
    assert "crawl-blocked" in {alarm.code for alarm in background}


# --- обход всех маршрутов чтения: имя строки Этапа 2 не встречается нигде -------------------

#: Что в ответе значит «строка Этапа 2»: домены и тексты мира, у доноров таких нет.
MARKS = ("brand-", "Interesting offer", "Let us talk", "Проверка advertisers")


@pytest.fixture
async def stage_two_rows(session: AsyncSession, world: World) -> None:
    """Вокруг переписки мира — всё остальное, что бывает у этапа, с именами `MARKS`."""
    await promoted(session, "brand-three.example.com", contact_status=ContactStatus.FORM_ONLY)
    await sender(session, "offers@brand-box.example.com", Stage.ADVERTISERS)
    session.add_all(
        [
            SuppressionModel(
                email="ceo@brand-four.example.com",
                reason=SuppressionReason.MANUAL,
                stage=Stage.ADVERTISERS,
            ),
            CrawlRunModel(
                host="brand-donor.example.com",
                status=CrawlStatus.DONE,
                outcome=CrawlOutcome.OK,
                stop_reason=StopReason.EXHAUSTED,
                pages_opened=10,
                articles=1,
            ),
        ]
    )
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        target="candidate:1",
        details={"рекламодатель": "brand-three.example.com"},
    )
    await session.commit()


def _read_routes(paths: dict[str, Any]) -> list[str]:
    """Маршруты чтения из схемы приложения: новый проверяется сам. Страницы отписки нет: она
    открыта адресату по метке, а не учётке."""
    return sorted(
        path for path, methods in paths.items() if "get" in methods and "unsubscribe" not in path
    )


def _with_numbers(path: str, world: World) -> str:
    """Адрес с номерами строк Этапа 2: остальные номера — «нет такой строки»."""
    numbers = {
        "thread_id": world.thread,
        "reply_id": world.reply,
        "message_id": world.sent,
        "letter_id": world.sent,
        "draft_id": world.draft,
    }
    return re.sub(r"\{([^}]*)\}", lambda found: str(numbers.get(found[1], 1)), path)


@pytest.mark.usefixtures("stage_two_rows", "providers_quiet", "nobody_down")
async def test_no_read_route_shows_a_stage_two_row(
    api_app: FastAPI,
    client: AsyncClient,
    world: World,
    donors_only: dict[str, str],
    admin_out: dict[str, str],
    admin_in: dict[str, str],
) -> None:
    """Страховка за списком мест: что бы ни отдал маршрут чтения — числа, список, файл, — имён
    строк Этапа 2 в ответе нет. Админ со снятым правом видит и экраны, закрытые роли: ящики,
    журнал. С правом те же имена на месте: обход видит их, если они есть."""
    routes = _read_routes(api_app.openapi()["paths"])
    seen_by_admin: set[str] = set()

    for path in routes:
        url = _with_numbers(path, world)
        for headers in (donors_only, admin_out):
            answer = await client.get(url, headers=headers)
            found = [mark for mark in MARKS if mark in answer.text]
            assert not found, f"{url}: {found} в ответе {answer.text[:300]}"
        opened = await client.get(url, headers=admin_in)
        if any(mark in opened.text for mark in MARKS):
            seen_by_admin.add(path)

    assert seen_by_admin >= {
        "/api/threads",
        "/api/threads/{thread_id}",
        "/api/advertisers/list",
        "/api/crawls",
        "/api/suppressions",
        "/api/senders",
        "/api/journal",
        "/api/agent/drafts",
    }, seen_by_admin


# --- журнал ---------------------------------------------------------------------------------


@pytest.fixture
async def noted(session: AsyncSession, world: World) -> set[str | None]:
    """Записи Этапа 2 и общие; ответ — цели тех, что видны без права «Рекламодатели»."""
    journal = AccessRepository(session)
    await journal.record(AuditAction.ADVERTISER_REVIEWED, target="candidate:1")
    await journal.record(AuditAction.ADVERTISER_REVIEWED, target="advertisers:promote")
    await journal.record(AuditAction.CONTACT_ADDED, target="advertiser:2")
    await journal.record(
        AuditAction.RUN_STARTED, target="crawl:3", details={"этап": "2 — обход донора"}
    )
    await journal.record(
        AuditAction.RUN_STARTED,
        target="crawl_backlog",
        details={"этап": "2 — обход доноров по фильтру"},
    )
    await journal.record(AuditAction.CRAWL_BACKLOG_DROPPED, target="crawl_backlog")
    await journal.record(
        AuditAction.RUN_STARTED,
        target="job:letters-build-advertisers-links",
        details={"действие": "сборка писем", "этап": "рекламодатели"},
    )
    await journal.record(
        AuditAction.USER_UPDATED, target=f"message:{world.queued}", stage=Stage.ADVERTISERS
    )
    # Строки без этапа в подробностях — как записи до его появления: этап по самой строке.
    await journal.record(AuditAction.LETTER_SENT, target=f"message:{world.sent}")
    await journal.record(AuditAction.LEAD_TAKEN, target=f"reply:{world.reply}")
    await journal.record(AuditAction.AGENT_DRAFT_DECIDED, target=f"agent_draft:{world.draft}")
    # Общие и донорские — остаются.
    await journal.record(AuditAction.LETTER_SENT, target=f"message:{world.donor_queued}")
    await journal.record(AuditAction.RUN_STARTED, target="run:7", details={"этап": "1"})
    await journal.record(AuditAction.THRESHOLDS_CHANGED, target="settings:1")
    await journal.record(AuditAction.USER_UPDATED, details={"кто": "консоль"})
    await session.commit()
    return {f"message:{world.donor_queued}", "run:7", "settings:1", None}


async def test_the_journal_leaves_stage_two_out_without_the_right(
    session: AsyncSession, noted: set[str | None]
) -> None:
    hidden = await read_journal(session, JournalQuery(with_advertisers=False, size=200))
    shown = await read_journal(session, JournalQuery(size=200))

    assert {entry.target for entry in hidden.entries} == noted
    assert hidden.total == 4
    assert shown.total == 15


async def test_the_stage_two_rule_keeps_a_record_without_details(session: AsyncSession) -> None:
    """Условие над пустыми подробностями — пустота, и фильтр не теряет запись молча."""
    await AccessRepository(session).record(AuditAction.LOGIN, target="user:1")
    await session.commit()

    found = await session.scalar(select(AuditLogModel.id).where(advertisers_entry().is_not(True)))

    assert found is not None


@pytest.mark.usefixtures("noted")
async def test_the_journal_says_what_is_hidden(
    client: AsyncClient, admin_out: dict[str, str], admin_in: dict[str, str]
) -> None:
    hidden = (await client.get("/api/journal?limit=200", headers=admin_out)).json()
    shown = (await client.get("/api/journal?limit=200", headers=admin_in)).json()

    assert hidden["advertisers_hidden"] is True
    assert hidden["sales_hidden"] is False
    assert shown["advertisers_hidden"] is False
    assert len(shown["entries"]) > len(hidden["entries"])
    assert all(entry["target"] != "candidate:1" for entry in hidden["entries"])


async def test_a_real_advertiser_form_is_not_counted_for_the_role(
    client: AsyncClient,
    session: AsyncSession,
    donors_only: dict[str, str],
    operator: dict[str, str],
) -> None:
    """Настоящая строка, без подмены чисел: форма рекламодателя не в числе «Форм» у роли."""
    await promoted(session, "shop.example.test", contact_status=ContactStatus.FORM_ONLY)
    await session.commit()

    hidden = (await client.get("/api/overview/work", headers=donors_only)).json()
    shown = (await client.get("/api/overview/work", headers=operator)).json()

    assert (hidden["forms"], shown["forms"]) == (0, 1)
