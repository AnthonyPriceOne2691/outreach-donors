"""«Повторить сейчас» у очистки, строки ожидания и предложения повтора в отчёте прогона, маршрут.

Ревью 20.10: строка «ждут очистки» считала новых, а кнопка брала по условию очистки — и готовых с
выдуманным вердиктом, и «ещё проверяет» до срока; срок, чья задача потерялась, был не виден вовсе;
в строках стояли слова лида с устаревшим «повторим следующей очисткой». Здесь: число у кнопки —
то, что она возьмёт (тем же условием, что у прохода), прошедший срок — «наступил» с кнопкой,
будущий — без, слова лида не показываются; маршрут берёт тем же условием и пишет число в журнал.

Оснастка — общая с проходом повтора (`test_sales_segment_again.py`): сайты, очередь, платная
ступень. Ниши, рынки, зоны и письменности разные, домены выдуманы, IDN — в punycode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.api.sales import segment as segment_api
from backend.features.core.domain import AuditAction, ContactStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import segment_report, segment_waits
from backend.features.sales.hunter_codes import STILL_CHECKING, StillCheckingError
from backend.features.sales.models import LeadStatus, SalesLeadModel
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.segment_run import Again, again
from backend.features.sales.verifier import Verdict
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_contacts_no_answer import Paid
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_again import (
    BAKERY,
    BOOKS,
    CLINIC,
    LEAST,
    TEA,
    YOGA,
    Candidate,
    Web,
    _accepted,
    _ago,
    _done_run,
    _fresh,
    _lead,
    _network,
    _ready,
    _run,
    _silent_sites,
    later,
    paid,
    web,
)
from tests.test_sales_segment_api import RUNS, headers, jobs
from tests.test_sales_verify_again import Later

#: Оснастка — та же, что у прохода повтора и маршрутов сбора.
__all__ = ["Later", "Web", "_network", "headers", "jobs", "later", "paid", "web"]


class Live:
    """Живой проверяльщик для очистки: годный вердикт, «ещё проверяет» — по маркеру; спрошенные —
    в список."""

    name = "hunter"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def verify(self, email: str) -> Verdict:
        self.asked.append(email)
        if email.startswith("still-checking"):
            raise StillCheckingError(STILL_CHECKING)
        return Verdict("valid", 90, units=1)


async def _cleaning_leads(session: AsyncSession, run: Any) -> dict[str, SalesLeadModel]:
    """Лиды прогона во всех причинах ожидания — и готовый с выдуманным вердиктом."""
    now = datetime.now(UTC)
    return {
        # Ещё не очищали — кнопка берёт.
        "fresh": await _lead(session, await _accepted(session, run, BAKERY), f"zakaz@{BAKERY}"),
        # Hunter ещё проверяет, срок прошёл, а перепроверки не было — кнопка берёт.
        "overdue": await _lead(
            session, await _accepted(session, run, CLINIC), f"still-checking@{CLINIC}",
            verify_since=now - timedelta(minutes=30), verify_again_at=now - timedelta(minutes=1),
        ),
        # Hunter ещё проверяет, срок впереди — сам; кнопка не трогает.
        "ahead": await _lead(
            session, await _accepted(session, run, BOOKS), f"still-checking@{BOOKS}",
            verify_since=now - timedelta(minutes=5), verify_again_at=now + timedelta(minutes=15),
        ),
        # Первый unknown ждёт своего дня — платная проверка раньше срока впустую.
        "unknown": await _lead(
            session, await _accepted(session, run, YOGA), f"hello@{YOGA}",
            verification_status="hunter:unknown", verified_at=now - timedelta(days=1),
        ),
        # Готов с выдуманным вердиктом — дело «Очистить» вкладки лидов, не отчёта прогона.
        "made_up": await _lead(
            session, await _accepted(session, run, TEA), f"shop@{TEA}",
            status=LeadStatus.READY, verification_status="fixture:valid",
        ),
    }  # fmt: skip


async def test_retry_of_cleaning_takes_what_the_button_offered(
    session: AsyncSession, later: Later, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ревью 20.10: строка «ждут очистки» считала новых, а кнопка брала по условию очистки —
    и готовых с выдуманным вердиктом, и «ещё проверяет» до срока. Теперь число у кнопки — то,
    что она возьмёт, и платных проверок — не больше него."""
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "live")
    run = await _done_run(session)
    leads = await _cleaning_leads(session, run)
    verifier = Live()

    offers = await segment_waits.run_offers(session, run, real=True, now=datetime.now(UTC))
    await again(session, run.id, verifier, Again.CLEANING)

    assert offers == [segment_waits.Offer("cleaning", 2, 2)]
    assert sorted(verifier.asked) == sorted([leads["fresh"].email, leads["overdue"].email])
    refreshed = await _run(session, run)
    assert refreshed.report["leads"]["cleaning"]["checked"] == 2


# --- отчёт: «ждут адреса» условием прохода, строки ожидания и предложения повтора ------------------


async def test_waiting_address_is_the_condition_of_the_pass(
    session: AsyncSession, web: Web
) -> None:
    """«Найти адреса принятым · N» — тем же условием, что у прохода: не искавшиеся, с наступившим
    сроком повтора и «не спросили», и лид из адреса, что уже есть на домене; не ответивший с
    повтором через день, только что не ответивший и «адреса нет» в счёт не входят."""
    run = await _done_run(session)
    await _silent_sites(session, run)
    await _ready(session, run)
    await _accepted(
        session, run, "chai-sample.in", contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(minutes=2),
    )  # fmt: skip

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


async def test_waits_name_why_when_and_what_the_button_takes(session: AsyncSession) -> None:
    """Строки — почему, сколько, срок и берёт ли их кнопка: прошедший срок — с кнопкой
    («наступил»), будущий — без; слова лида не показываются (ревью 20.10: старые «повторим
    следующей очисткой» обещали то, чего нет). Число у кнопки — сумма строк, которые она берёт."""
    run = await _done_run(session)
    sites = await _silent_sites(session, run)
    now = datetime.now(UTC)
    passed, future = now - timedelta(minutes=1), now + timedelta(minutes=12)
    await _lead(
        session, await _accepted(session, run, "chai-sample.in"), "still-checking@chai-sample.in",
        verify_since=now - timedelta(minutes=30), verify_again_at=passed,
    )  # fmt: skip
    await _lead(
        session, await _accepted(session, run, "kahve-sample.com.tr"),
        "still-checking@kahve-sample.com.tr",
        verify_since=now - timedelta(minutes=10), verify_again_at=future,
    )  # fmt: skip
    unknown_at = now - timedelta(days=1)
    await _lead(
        session,
        await _accepted(session, run, "xn--musique-cole-ieb.ca"),
        "contact@xn--musique-cole-ieb.ca",
        verification_status="fixture:unknown",
        verified_at=unknown_at,
    )
    for host in ("orto-sample.it", "vinho-sample.pt"):
        await _lead(
            session, await _accepted(session, run, host), f"ola@{host}",
            cleaning_note="проверка не выполнена: провайдер не ответил (HTTP 503) — повторим "
            "следующей очисткой",
        )  # fmt: skip
    await _lead(session, await _accepted(session, run, "sauna-sample.fi"), "moi@sauna-sample.fi")  # fmt: skip

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    due_site, later_site = (
        await _fresh(session, sites["due"]),
        await _fresh(session, sites["later"]),
    )
    assert due_site.contact_attempted_at is not None
    assert later_site.contact_attempted_at is not None
    day = (unknown_at + timedelta(days=7)).strftime("%d.%m.%Y")
    Wait = segment_waits.Wait
    assert view.waits == [
        Wait("address", segment_waits.SILENT, 1, due_site.contact_attempted_at + LEAST),
        Wait(
            "address",
            segment_waits.SILENT,
            1,
            later_site.contact_attempted_at + timedelta(days=1),
            again=False,
        ),
        Wait("cleaning", "Hunter ещё проверяет адрес", 1, passed),
        Wait("cleaning", "Hunter ещё проверяет адрес", 1, future, again=False),
        Wait(
            "cleaning",
            f"адрес не подтверждён (unknown) — перепроверка не раньше {day}",
            1,
            again=False,
        ),
        Wait("cleaning", segment_waits.NOT_CLEANED, 1),
        Wait("cleaning", segment_waits.OTHER, 2),
    ]
    assert not any("следующей очисткой" in wait.words for wait in view.waits)
    assert view.offers == [
        segment_waits.Offer("addresses", 1, 0),
        segment_waits.Offer("cleaning", 4, 0),
    ]
    for offered in view.offers:
        kind = "address" if offered.what == "addresses" else "cleaning"
        taken = sum(w.count for w in view.waits if w.kind == kind and w.again)
        assert taken == offered.count


async def test_unasked_sites_show_with_the_paid_step_and_its_budget(
    session: AsyncSession, paid: Paid
) -> None:
    """«Не спросили» — строкой и в числе кнопки, только когда повтор их возьмёт; платных поисков
    у кнопки — не больше остатка сметы."""
    run = await _done_run(session, searches=1)
    await _silent_sites(session, run)

    view = await segment_report.run_view(session, run.id)

    assert view is not None
    assert (
        segment_waits.Wait("address", "квота платного поиска — повтор следующим проходом", 1)
        in view.waits
    )
    assert view.offers == [segment_waits.Offer("addresses", 2, 1)]


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
    refreshed = await _run(session, run)
    assert (refreshed.job_id, refreshed.finished_at) == (segment_job_id(run.id), None)
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    # Сколько возьмёт и до скольких платных запросов — тем же числом, что рядом с кнопкой.
    assert journal.details == {
        "действие": "повтор сбора базы продаж: поиск адреса",
        "прогон": run.id,
        "возьмёт": 1,
        "платных запросов Hunter до": 0,
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
            "Повторять нечего: сайтов, чей срок повтора поиска адреса настал, в прогоне нет — "
            "у остальных повтор пойдёт сам в свой срок",
        ),
        (
            "cleaning",
            "Повторять нечего: лидов прогона, которых возьмёт очистка сейчас, нет — у остальных "
            "перепроверка пойдёт сама в свой срок",
        ),
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
    """Срок, который не настал, кнопка не берёт: и сайт с повтором через день, и лид, чья
    перепроверка впереди, — «повторять нечего»."""
    run = await _done_run(session)
    await _accepted(session, run, YOGA)  # первый поиск — не повтор
    await _accepted(
        session, run, CLINIC, contact_status=ContactStatus.NO_ANSWER, contact_tries=2,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    await _lead(
        session, await _accepted(session, run, BOOKS), f"still-checking@{BOOKS}",
        verify_since=_ago(minutes=5), verify_again_at=datetime.now(UTC) + timedelta(minutes=15),
    )  # fmt: skip

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


async def test_the_run_report_carries_the_waits_and_the_offers(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    """Прошедший срок — в отчёте («наступил» и кнопка), а не спрятан (ревью 20.10: срок, чья
    задача потерялась, не был виден вовсе); рядом — что возьмёт кнопка."""
    run = await _done_run(session)
    sites = await _silent_sites(session, run)

    detail = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()

    due_site, later_site = (
        await _fresh(session, sites["due"]),
        await _fresh(session, sites["later"]),
    )
    assert due_site.contact_attempted_at is not None
    assert later_site.contact_attempted_at is not None

    def iso(moment: datetime) -> str:
        return moment.isoformat().replace("+00:00", "Z")

    assert detail["waits"] == [
        {
            "kind": "address",
            "words": "сайт не ответил поиску адреса",
            "count": 1,
            "at": iso(due_site.contact_attempted_at + LEAST),
            "again": True,
        },
        {
            "kind": "address",
            "words": "сайт не ответил поиску адреса",
            "count": 1,
            "at": iso(later_site.contact_attempted_at + timedelta(days=1)),
            "again": False,
        },
    ]
    assert detail["again"] == [{"what": "addresses", "count": 1, "paid": 0}]
    assert detail["waiting_address"] == 3
    count = await session.scalar(select(func.count()).select_from(Candidate))
    assert count == 5
