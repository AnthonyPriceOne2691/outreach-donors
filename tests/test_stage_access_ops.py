"""Право «Продажи» на служебных экранах — решение Anthony 10.10.2026, П2б.

П2 закрыло правом письма, переписки, ответы и агента продаж, а служебные экраны оставались
открыты: стоп-лист показывал и снимал записи этапа продаж, экран доменов — ящики продаж,
исход задачи продаж читался по номеру, расход — статьями модели продаж, сторож — тревогами
о почте продаж. Теперь без права «Продажи» их нет ни в списках, ни в числах, а действие над
строкой продаж — 403 словами.

Каждое правило — с двух сторон: без права — скрыто или отказ; с ним — как до П2б. Учётки —
операторы (право «Продажи» у них в роли и снимается поимённо), экран доменов — админы:
право `senders` есть только у них.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.api.letters import once
from backend.api.sales.queue import build_job_id
from backend.api.settings import routes as settings_routes
from backend.features.ahrefs.client import AhrefsError
from backend.features.core import stages, usage
from backend.features.core.domain import (
    MessageStatus,
    SenderStatus,
    Stage,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import SendingDomainModel
from backend.features.core.stages import MailPolicy
from backend.features.ops import job_outcome as outcome_module
from backend.features.ops import silence
from backend.features.ops.alarms import Alarm
from backend.features.ops.job_outcome import JobOutcome, job_outcome, sales_job
from backend.features.sales.agent.notify import NOTICE_JOB
from backend.features.sales.agent.parts import (
    DRAFT_OPERATION,
    JUDGE_OPERATION,
    SITUATION_OPERATION,
)
from backend.features.sales.cleaning import OPERATION as VERIFY_OPERATION
from backend.features.sales.handoff import HANDOFF_JOB
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.features.sales.usage_cap import OPERATIONS as SALES_MODEL_CALLS
from backend.shared.queue import (
    BUILD_JOB,
    CONTACTS_JOB,
    CRAWL_JOB,
    CRAWL_QUEUE_NAME,
    PARSE_JOB,
    QUEUE_NAME,
    RUN_JOB,
    SALES_QUEUE_NAME,
    SALES_REPLY_JOB,
    SEND_QUEUE_JOB,
    parse_job_id,
    sales_job_id,
)
from backend.workers.agent_jobs import DRAFT_JOB, draft_job_id
from httpx import AsyncClient
from rq.job import Job, JobStatus
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender
from tests.test_silence_watchdog import _letter as donor_letter

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

CLOSED = "— только с правом «Продажи»"
#: Этапы учётки без права «Продажи» (`access.permissions.visible_stages`).
NO_SALES = frozenset({Stage.DONORS, Stage.ADVERTISERS})


@pytest.fixture
async def outsider(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор с отправкой, у которого право «Продажи» снято поимённо."""
    await make_user("outsider@team.example.com", permissions={"sales": False, "send": True})
    return bearer(await sign_in("outsider@team.example.com"))


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор с отправкой: право «Продажи» у него в роли."""
    await make_user("seller@team.example.com", permissions={"send": True})
    return bearer(await sign_in("seller@team.example.com"))


# --- стоп-лист: записи этапа продаж -------------------------------------------------------

SUPPRESSIONS = "Записи стоп-листа продаж"


@dataclass(frozen=True, slots=True)
class StopWorld:
    #: Запись без этапа — держит все этапы; решение адресата (отписка).
    everyone: int
    #: Запись этапа продаж, срок которой вышел.
    sales: int
    #: Запись этапа продаж — жалоба лида: решение адресата.
    sales_complaint: int


@pytest.fixture
async def stop_world(session: AsyncSession) -> StopWorld:
    domain = DomainModel(host="stop-donor.example.com")
    session.add(domain)
    await session.flush()
    rows = [
        SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED),
        SuppressionModel(
            email="lead@lead-co.example.com",
            reason=SuppressionReason.MANUAL,
            stage=Stage.SALES,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
        SuppressionModel(
            email="ceo@lead-two.example.com",
            reason=SuppressionReason.COMPLAINED,
            stage=Stage.SALES,
        ),
    ]
    session.add_all(rows)
    await session.commit()
    everyone, sales, complaint = rows
    return StopWorld(everyone=everyone.id, sales=sales.id, sales_complaint=complaint.id)


async def test_stop_list_shows_sales_entries_and_counts_them_only_with_the_right(
    client: AsyncClient, stop_world: StopWorld, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Числа шапки — из тех же строк: без права запись продаж не считается ни в «всего»,
    ни в решениях адресата, ни в истёкших."""
    hidden = (await client.get("/api/suppressions", headers=outsider)).json()
    shown = (await client.get("/api/suppressions", headers=seller)).json()

    assert [row["id"] for row in hidden["rows"]] == [stop_world.everyone]
    assert (hidden["total"], hidden["donor_decisions"], hidden["expired"]) == (1, 1, 0)
    assert {row["id"] for row in shown["rows"]} == {
        stop_world.everyone,
        stop_world.sales,
        stop_world.sales_complaint,
    }
    assert (shown["total"], shown["donor_decisions"], shown["expired"]) == (3, 2, 1)


async def test_a_sales_entry_is_removed_only_with_the_right(
    client: AsyncClient, stop_world: StopWorld, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    path = f"/api/suppressions/{stop_world.sales}/remove"

    refused = await client.post(path, json={"reason": "клиент вернулся"}, headers=outsider)
    removed = await client.post(path, json={"reason": "клиент вернулся"}, headers=seller)

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"{SUPPRESSIONS} {CLOSED}"
    assert removed.status_code == 200, removed.text


async def test_an_entry_of_every_stage_stays_open_without_the_right(
    client: AsyncClient, stop_world: StopWorld, outsider: dict[str, str]
) -> None:
    """Запись без этапа держит и доноров: её снимают своим правом, как до П2б."""
    response = await client.post(
        f"/api/suppressions/{stop_world.everyone}/remove",
        json={"reason": "донор попросил писать"},
        headers=outsider,
    )

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("stage", "opened"), [("sales", False), (None, True)], ids=["этап продаж", "все этапы"]
)
async def test_adding_a_sales_entry_needs_the_right(
    client: AsyncClient,
    outsider: dict[str, str],
    seller: dict[str, str],
    stage: str | None,
    opened: bool,
) -> None:
    body = {"target": "partner@client-co.example.com", "reason": "manual", "stage": stage}

    without = await client.post("/api/suppressions", json=body, headers=outsider)
    added = await client.post(
        "/api/suppressions", json={**body, "target": "boss@client-co.example.com"}, headers=seller
    )

    assert without.status_code == (200 if opened else 403), without.text
    if not opened:
        assert without.json()["detail"] == f"{SUPPRESSIONS} {CLOSED}"
    assert added.status_code == 200, added.text


# --- домены рассылки: ящики продаж -------------------------------------------------------

SENDERS = "Ящики продаж"
DONOR_BOX, SALES_BOX = "anna@mail-donors.example.com", "hello@mail-sales.example.com"
#: Ящик донора на домене, строка которого записана за продажами: домен закрыт для доноров.
SHARED_BOX = "bob@mail-shared.example.com"


@dataclass(frozen=True, slots=True)
class BoxWorld:
    donor: int
    sales: int


@pytest.fixture
async def box_world(session: AsyncSession) -> BoxWorld:
    donor = await sender(session, DONOR_BOX, Stage.DONORS)
    sales = await sender(session, SALES_BOX, Stage.SALES)
    await sender(session, SHARED_BOX, Stage.DONORS)
    session.add_all(
        [
            SendingDomainModel(domain="mail-sales.example.com", stage=Stage.SALES, daily_limit=40),
            SendingDomainModel(domain="mail-shared.example.com", stage=Stage.SALES, daily_limit=10),
        ]
    )
    await session.commit()
    return BoxWorld(donor=donor.id, sales=sales.id)


@pytest.fixture
async def admin_outsider(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Админ, у которого право «Продажи» снято: правило — о праве, а не о роли."""
    await make_user("admin-out@team.example.com", role=UserRole.ADMIN, permissions={"sales": False})
    return bearer(await sign_in("admin-out@team.example.com"))


@pytest.fixture
async def admin_seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin-in@team.example.com", role=UserRole.ADMIN)
    return bearer(await sign_in("admin-in@team.example.com"))


async def test_sending_screen_shows_sales_boxes_only_with_the_right(
    client: AsyncClient,
    box_world: BoxWorld,
    admin_outsider: dict[str, str],
    admin_seller: dict[str, str],
) -> None:
    """Без права — ни ящика продаж, ни его домена, ни направления продаж, ни их счёта.
    Строка домена за продажами, на котором пишет донор, остаётся: она закрывает донору
    домен, и без неё экран звал бы закрытый домен открытым."""
    hidden = (await client.get("/api/senders", headers=admin_outsider)).json()
    shown = (await client.get("/api/senders", headers=admin_seller)).json()

    assert {box["email"] for box in hidden["senders"]} == {DONOR_BOX, SHARED_BOX}
    assert hidden["enabled_domains"] == 2
    assert [row["domain"] for row in hidden["domains"]] == ["mail-shared.example.com"]
    assert [one["stage"] for one in hidden["directions"]] == ["donors", "advertisers"]
    assert {box["email"] for box in shown["senders"]} == {DONOR_BOX, SALES_BOX, SHARED_BOX}
    assert shown["enabled_domains"] == 3
    assert [row["domain"] for row in shown["domains"]] == [
        "mail-sales.example.com",
        "mail-shared.example.com",
    ]
    assert [one["stage"] for one in shown["directions"]] == ["donors", "advertisers", "sales"]


@pytest.mark.parametrize("action", ["enable", "disable"])
async def test_a_sales_box_is_switched_only_with_the_right(
    client: AsyncClient,
    box_world: BoxWorld,
    admin_outsider: dict[str, str],
    admin_seller: dict[str, str],
    action: str,
) -> None:
    path = f"/api/senders/{box_world.sales}/{action}"

    refused = await client.post(path, json={"reason": "проверка"}, headers=admin_outsider)
    switched = await client.post(path, json={"reason": "проверка"}, headers=admin_seller)
    donor = await client.post(
        f"/api/senders/{box_world.donor}/{action}",
        json={"reason": "проверка"},
        headers=admin_outsider,
    )

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"{SENDERS} {CLOSED}"
    assert switched.status_code == 200, switched.text
    assert donor.status_code == 200, donor.text


# --- исход фоновой задачи: задачи продаж ---------------------------------------------------

JOBS = "Задачи продаж"
#: Задача очистки лидов продаж — путём строкой (`sales/clean_jobs.py`, PR #303 продаж).
CLEAN_JOB = "backend.features.sales.clean_jobs.clean_sales_leads"

#: Задачи продаж; у каждого признака — случай, где решает он один: путь модуля продаж в чужой
#: очереди, черновик к ответу лида (путь общий, очередь продаж), пачка и сборка писем (номер).
SALES_JOBS = [
    pytest.param(build_job_id(5), QUEUE_JOB, SALES_QUEUE_NAME, id="сборка очереди продаж"),
    pytest.param(build_job_id(5), QUEUE_JOB, QUEUE_NAME, id="путь продаж — в любой очереди"),
    pytest.param("sales-clean-5", CLEAN_JOB, SALES_QUEUE_NAME, id="очистка лидов"),
    pytest.param(sales_job_id(7), SALES_REPLY_JOB, SALES_QUEUE_NAME, id="разбор ответа лида"),
    pytest.param("handoff-1", HANDOFF_JOB, SALES_QUEUE_NAME, id="передача лида"),
    pytest.param("notice-1", NOTICE_JOB, SALES_QUEUE_NAME, id="уведомление агента"),
    pytest.param(draft_job_id(7), DRAFT_JOB, SALES_QUEUE_NAME, id="черновик к ответу лида"),
    pytest.param(
        once.build_job_id(Stage.SALES, "links"), BUILD_JOB, QUEUE_NAME, id="сборка писем продаж"
    ),
    pytest.param(
        once.send_job_id(Stage.SALES, "links"), SEND_QUEUE_JOB, QUEUE_NAME, id="пачка продаж"
    ),
]
OTHER_JOBS = [
    pytest.param("run-42", RUN_JOB, QUEUE_NAME, id="прогон"),
    pytest.param(
        once.build_job_id(Stage.DONORS, "links"), BUILD_JOB, QUEUE_NAME, id="сборка доноров"
    ),
    pytest.param(
        once.send_job_id(Stage.ADVERTISERS, "niche"), SEND_QUEUE_JOB, QUEUE_NAME, id="пачка Э2"
    ),
    pytest.param(parse_job_id(7), PARSE_JOB, QUEUE_NAME, id="разбор цены"),
    pytest.param(draft_job_id(8), DRAFT_JOB, QUEUE_NAME, id="черновик донору"),
    pytest.param("contacts-1", CONTACTS_JOB, QUEUE_NAME, id="поиск контактов"),
    pytest.param("crawl-3-ab12cd34", CRAWL_JOB, CRAWL_QUEUE_NAME, id="обход"),
]


@pytest.mark.parametrize(("job_id", "path", "queue"), SALES_JOBS)
def test_a_sales_job_is_known_by_what_it_is(job_id: str, path: str, queue: str) -> None:
    assert sales_job(job_id, path, queue)


@pytest.mark.parametrize(("job_id", "path", "queue"), OTHER_JOBS)
def test_other_jobs_are_not_sales(job_id: str, path: str, queue: str) -> None:
    assert not sales_job(job_id, path, queue)


@pytest.mark.parametrize(
    ("job_id", "path", "queue", "stage"),
    [
        (once.send_job_id(Stage.SALES, "links"), SEND_QUEUE_JOB, QUEUE_NAME, Stage.SALES),
        (build_job_id(5), QUEUE_JOB, SALES_QUEUE_NAME, Stage.SALES),
        ("run-42", RUN_JOB, QUEUE_NAME, None),
    ],
    ids=["пачка продаж", "сборка очереди продаж", "прогон"],
)
def test_the_outcome_carries_the_stage_of_the_job(
    monkeypatch: pytest.MonkeyPatch, job_id: str, path: str, queue: str, stage: Stage | None
) -> None:
    """Этап исхода — по настоящей задаче rq: путь, номер и очередь, откуда она пришла."""
    conn: Any = object()
    job = Job.create(path, args=(5,), connection=conn, id=job_id, origin=queue)
    monkeypatch.setattr(job, "get_status", lambda refresh=True: JobStatus.FINISHED)
    monkeypatch.setattr(job, "latest_result", lambda: None)
    monkeypatch.setattr(outcome_module.Job, "fetch", lambda _job_id, connection: job)

    found = job_outcome(job_id, redis=conn)

    assert found is not None
    assert found.stage is stage


@pytest.fixture
def outcomes(monkeypatch: pytest.MonkeyPatch) -> dict[str, JobOutcome]:
    known = {
        "sales-queue-5": JobOutcome(
            job_id="sales-queue-5",
            kind="сборка очереди продаж",
            state="done",
            report={"prepared": 3},
            stage=Stage.SALES,
        ),
        "run-42": JobOutcome(job_id="run-42", kind="прогон", state="running"),
    }
    monkeypatch.setattr("backend.api.jobs.routes.job_outcome", known.get)
    return known


@pytest.mark.usefixtures("outcomes")
async def test_a_sales_job_outcome_is_read_only_with_the_right(
    client: AsyncClient, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    refused = await client.get("/api/jobs/sales-queue-5", headers=outsider)
    read = await client.get("/api/jobs/sales-queue-5", headers=seller)

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == f"{JOBS} {CLOSED}"
    assert read.status_code == 200, read.text
    assert read.json()["report"] == {"prepared": 3}


@pytest.mark.usefixtures("outcomes")
async def test_other_jobs_and_unknown_numbers_answer_as_before_without_the_right(
    client: AsyncClient, outsider: dict[str, str]
) -> None:
    """Прогон — исход как был; задачи, которой нет, — «не найдено», а не отказ в праве."""
    run = await client.get("/api/jobs/run-42", headers=outsider)
    unknown = await client.get("/api/jobs/letters-send-sales-links", headers=outsider)

    assert run.status_code == 200, run.text
    assert unknown.status_code == 404, unknown.text


# --- расход: операции продаж ------------------------------------------------------------

#: Операция → единиц: доноры, общая отправка и продажи вперемешку.
SPENT = {
    "batch_metrics": 1000,
    "reply_parse": 100,
    "contacts_search": 2,
    "sales_draft": 40,
    "sales_reply_kind": 10,
    "sales_verify": 3,
}


@pytest.fixture
async def spent(session: AsyncSession) -> None:
    for operation, units in SPENT.items():
        usage.record(session, operation=operation, units=units)
    await session.commit()


@pytest.fixture
def providers_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остатки у провайдеров не спрашиваются: тест — о своей таблице расхода."""

    class SilentAhrefs:
        async def limits_and_usage(self) -> None:
            raise AhrefsError("тест: остаток не спрашиваем")

        async def aclose(self) -> None:
            return None

    async def no_serp() -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(settings_routes, "AhrefsClient", SilentAhrefs)
    monkeypatch.setattr(settings_routes, "_serp_balance", no_serp)


@pytest.mark.usefixtures("spent", "providers_quiet")
async def test_spend_without_the_right_leaves_sales_out_of_lines_and_totals(
    client: AsyncClient, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Без права статьи, суммы по провайдерам и итог — без продаж и сходятся между собой:
    расход продаж не вычесть из итога. Что это не весь счёт — `sales_hidden`."""
    hidden = (await client.get("/api/usage", headers=outsider)).json()
    shown = (await client.get("/api/usage", headers=seller)).json()

    assert {line["operation"] for line in hidden["articles"]} == {
        "batch_metrics",
        "reply_parse",
        "contacts_search",
    }
    assert hidden["units_by_provider"] == {"ahrefs": 1000, "llm": 100, "hunter": 2}
    assert hidden["total_units"] == sum(line["units"] for line in hidden["articles"]) == 1102
    assert hidden["sales_hidden"] is True
    assert {line["operation"] for line in shown["articles"]} == set(SPENT)
    assert shown["units_by_provider"] == {"ahrefs": 1000, "llm": 150, "hunter": 5}
    assert shown["total_units"] == sum(SPENT.values())
    assert shown["sales_hidden"] is False


def test_every_operation_the_sales_module_records_is_a_sales_operation() -> None:
    """Модель продаж (вид ответа, письма очереди, агент) и проверка адресов лидов — продажи;
    отправка письма — общая на все этапы и в продажи не входит."""
    recorded = {
        *SALES_MODEL_CALLS,
        DRAFT_OPERATION,
        JUDGE_OPERATION,
        SITUATION_OPERATION,
        VERIFY_OPERATION,
    }

    assert recorded <= usage.SALES_OPERATIONS
    assert "letter_send" not in usage.SALES_OPERATIONS


# --- сторож: тревоги о почте продаж --------------------------------------------------------


@pytest.fixture
async def watched(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> list[Stage]:
    """Сторож почты продаж — в политике подставного модуля; ящик продаж на паузе — тревога
    «все на паузе», а донорское письмо без события доставки — общая тревога. Возвращает,
    о политике каких этапов сторож спросил мост."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    asked: list[Stage] = []
    policy_of = stages.mail_policy

    async def counted(session: AsyncSession, stage: Stage, what: str) -> MailPolicy:
        asked.append(stage)
        return await policy_of(session, stage, what)

    monkeypatch.setattr(stages, "mail_policy", counted)
    box = await sender(session, SALES_BOX, Stage.SALES)
    box.status = SenderStatus.PAUSED
    await _silent_letter(session)
    await session.commit()
    return asked


async def _silent_letter(session: AsyncSession) -> None:
    """Письмо донору ушло вчера, а платформа не сказала о нём ни слова."""
    await donor_letter(
        session,
        status=MessageStatus.SENT,
        sent_at=datetime.now(UTC) - timedelta(days=1),
        provider_message_id="sg-silent-1",
        number=71,
    )


@pytest.fixture
def nobody_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def answered() -> Alarm | None:
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", answered)


@pytest.mark.usefixtures("nobody_down")
async def test_watchdog_shows_sales_mail_alarms_only_with_the_right(
    client: AsyncClient, watched: list[Stage], outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Общая тревога — всем, без этапа; тревога о почте продаж — с правом, со своим этапом."""
    hidden = (await client.get("/api/watchdog", headers=outsider)).json()["alarms"]
    shown = (await client.get("/api/watchdog", headers=seller)).json()["alarms"]

    assert [(one["code"], one["stage"]) for one in hidden] == [("delivery-silence", None)]
    assert {(one["code"], one["stage"]) for one in shown} == {
        ("delivery-silence", None),
        ("all-paused:sales", "sales"),
    }


async def test_without_the_right_the_sales_module_is_not_asked_about_its_mail(
    session: AsyncSession, watched: list[Stage]
) -> None:
    """Сторож считает только видимые этапы: о политике почты продаж мост не спрашивается."""
    found = await silence.alarms(session, stages=NO_SALES, now=datetime.now(UTC))

    assert watched == [Stage.DONORS, Stage.ADVERTISERS]
    assert {alarm.code for alarm in found} == {"delivery-silence"}
