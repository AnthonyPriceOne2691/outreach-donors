"""Платная часть повтора поиска адреса (`segment_paid`): резерв упавшего куска и срок прохода после
отказа провайдера — находки ревью #426.

- **Резерв упавшего куска сводится весь** — в «принято, но не записано». Прежняя оценка «журнал
  расхода + сайты куска без исхода» теряла оплаченное: исход пишется домену во всех прогонах
  (`attempts.record`), и проход другого прогона с теми же сайтами между сбоем и сведением давал им
  исход. Пересчёт — в сторону траты.
- **После отказа провайдера** (квота, частота, сбой) срок прохода по сроку — не раньше
  `SALES_SEGMENT_AGAIN_MIN` после отказа, а не через минуту: сроки задержанных сайтов уже настали, и
  проход через минуту упёрся бы в ту же стену. Задача на срок, пришедшая в паузу, — пропуск и новый
  срок, а не проход.

Сайты — `httpx.MockTransport` (`test_sales_segment_again.Web`): главная с адресом своим языком или
без него, обрыв соединения; DNS — «почта есть»; платная ступень — подделка со счётом обращений;
очередь — подделка с правилами rq 2.12. Ниши, рынки, зоны и письменности разные, домены выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.contacts import mx
from backend.features.contacts.provider import (
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.core.domain import ContactStatus
from backend.features.sales import cleaning, deferred, segment_again, segment_jobs, segment_paid
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import Stage
from backend.features.sales.segment_queue import SegmentContactQueue, paid_left
from backend.features.sales.segment_run import Again, again
from backend.features.sales.segment_spend import pending, unrecorded
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_contacts_no_answer import Paid
from tests.test_sales_segment_again import (
    BAKERY,
    BOOKS,
    CLINIC,
    LEAST,
    Web,
    _accepted,
    _ago,
    _done_run,
    _last_try,
    _paid_ready,
    _run,
    _sites,
)
from tests.test_sales_segment_run import make_run
from tests.test_sales_verify_again import Later

#: Сайты без адреса на главной — платная ступень по ним зовётся: автошкола (.at), мастерская
#: (.com.ar), гостиница (.jp).
PLAIN = ["fahrschule-sample.at", "taller-sample.com.ar", "ryokan-sample.jp"]


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS — у всех доменов почта есть."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(cleaning, "mail_route", route)


@pytest.fixture
def later(monkeypatch: pytest.MonkeyPatch) -> Later:
    found = Later()
    monkeypatch.setattr(deferred, "sales_bulk_queue", lambda: found)
    return found


class Refuses:
    """Платная ступень, которая отказывает каждому запросу своим отказом: квота, частота, сбой."""

    name = "fake"

    def __init__(self, refusal: Exception) -> None:
        self.refusal = refusal
        self.calls: list[str] = []

    async def quota(self) -> object:
        raise AssertionError("остаток узнаёт `paid_step`, подменённый в тесте")

    async def find_emails(self, host: str) -> list[object]:
        self.calls.append(host)
        raise self.refusal


# --- резерв упавшего куска -----------------------------------------------------------------------


async def test_a_crashed_chunk_is_settled_whole_though_another_run_touched_its_sites(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Hunter ответил на три запроса куска, воркер упал до коммита пачки — ни исхода, ни строки
    журнала. До следующего прохода прогона те же сайты прошёл повтор прогона другой гипотезы без
    платной ступени: сайты ожили, адреса нашлись. Прежняя оценка: журнал 0 + (резерв 3 − «исход
    после резерва» 3) = 0 — три оплаченных запроса пропадали из счёта, и остаток сметы снова
    разрешал их потратить. Теперь весь резерв — «принято без записи», остаток сметы — 10 − 3."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    hosts = [BAKERY, CLINIC, BOOKS]
    _sites(monkeypatch, Web(down=set(hosts)))
    run = await _done_run(session, searches=10)
    for host in hosts:
        await _last_try(session, run, host)
    saving = SegmentContactQueue.save

    async def worker_died(*_args: object, **_kwargs: object) -> int:
        raise ConnectionError("воркер упал посреди пачки: Hunter ответил, исход не записан")

    monkeypatch.setattr(SegmentContactQueue, "save", worker_died)
    with pytest.raises(ConnectionError):
        await again(session, run.id, FixtureVerifier(), Again.DUE)
    monkeypatch.setattr(SegmentContactQueue, "save", saving)
    crashed = await _run(session, run)
    assert (sorted(paid.calls), pending(crashed.report)) == (sorted(hosts), 3)

    # Кандидат — один на домен в гипотезе: те же сайты принял прогон другой гипотезы.
    owner = SalesHypothesisModel(name="соседняя гипотеза", description="клиники и ателье")
    session.add(owner)
    await session.flush()
    other = await make_run(session, owner)
    other.stage, other.finished_at = Stage.DONE, _ago(hours=1)
    await session.commit()
    for host in hosts:
        await _accepted(
            session, other, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
            contact_attempted_at=_ago(hours=1),
        )  # fmt: skip
    _sites(monkeypatch, Web())
    await again(session, other.id, FixtureVerifier(), Again.DUE)
    assert len(paid.calls) == 3  # у второго прогона платного поиска в смете нет

    await again(session, run.id, FixtureVerifier(), Again.DUE)  # следующий проход прогона

    settled = await _run(session, run)
    assert (pending(settled.report), unrecorded(settled.report)) == (0, 3)
    assert paid_left(settled.report, settled.estimate) == 10 - 3


# --- срок после отказа провайдера ------------------------------------------------------------------


@pytest.mark.parametrize(
    "refusal",
    [
        ProviderQuotaError("квота исчерпана"),
        ProviderRateLimitError("частота: 429 и после повторов"),
        ProviderError("сервис не ответил: 503"),
    ],
    ids=["квота", "частота", "сбой"],
)
async def test_after_a_provider_refusal_the_next_pass_waits_the_setting(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later, refusal: Exception
) -> None:
    """Отказ держит остальных сайтов прохода (`walk` → `held`), а их сроки уже настали: срок
    следующего прохода ставился через минуту (`deferred.SOONEST`), и проход упирался в ту же квоту,
    частоту или сбой — каждую минуту. Теперь — не раньше `SALES_SEGMENT_AGAIN_MIN` после отказа."""
    _sites(monkeypatch, Web())
    refuses = Refuses(refusal)
    _paid_ready(monkeypatch, refuses)
    run = await _done_run(session, searches=1)
    for host in PLAIN:
        await _accepted(
            session, run, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
            contact_attempted_at=_ago(hours=1),
        )  # fmt: skip
    started = datetime.now(UTC)

    await again(session, run.id, FixtureVerifier(), Again.DUE)
    # Как после прохода без сбоя (`segment_jobs.run_once`): ближе минуты — нельзя.
    await segment_again.plan(session, run.id, now=datetime.now(UTC), soonest=deferred.SOONEST)

    assert refuses.calls == [PLAIN[0]]
    assert [(path, args) for _, path, args, _ in later.planned] == [
        (segment_again.TRIGGER, (run.id,))
    ]
    assert later.planned[0][0] >= started + LEAST


async def test_after_the_chunks_of_a_pass_the_next_pass_comes_soon(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, later: Later
) -> None:
    """Кусков набралось на проход, а провайдер не отказывал — остальных продолжит следующий проход
    через минуту: паузы у провайдера не просили."""
    _sites(monkeypatch, Web())
    _paid_ready(monkeypatch, Paid())
    monkeypatch.setattr(segment_paid, "CHUNKS", 1)
    monkeypatch.setattr("backend.features.contacts.search.BATCH", 1)
    run = await _done_run(session, searches=5)
    for host in PLAIN:
        await _accepted(
            session, run, host, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
            contact_attempted_at=_ago(hours=1),
        )  # fmt: skip

    await again(session, run.id, FixtureVerifier(), Again.DUE)
    now = datetime.now(UTC)
    await segment_again.plan(session, run.id, now=now, soonest=deferred.SOONEST)

    assert [when for when, *_ in later.planned] == [now + deferred.SOONEST]


async def test_a_term_job_in_the_pause_puts_no_pass(session: AsyncSession, later: Later) -> None:
    """Задача на срок, поставленная до отказа (человек нажал «Повторить сейчас» раньше её срока, и
    проход упёрся в отказ), пришла в паузу: пропуск словами и новый срок, а не проход. Пауза
    прошла — проход."""
    run = await _done_run(session)
    await _accepted(
        session, run, BAKERY, contact_status=ContactStatus.NO_ANSWER, contact_tries=1,
        contact_attempted_at=_ago(hours=1),
    )  # fmt: skip
    now = datetime.now(UTC)
    run.report = {"contacts": {segment_paid.REFUSED_AT: (now - timedelta(minutes=5)).isoformat()}}
    await session.commit()

    paused = await segment_jobs.put_due(session, later, run.id, now=now)

    assert paused["skipped"] == segment_jobs.PAUSED
    assert (later.enqueued, [when for when, *_ in later.planned]) == ([], [now + LEAST])
    after = await segment_jobs.put_due(session, later, run.id, now=now + LEAST)
    assert after == {"run": run.id, "job_id": segment_job_id(run.id)}
    assert later.enqueued == [(SEGMENT_JOB, (run.id, Again.DUE.value), segment_job_id(run.id))]


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("2026-10-21T10:00:00+00:00", datetime(2026, 10, 21, 10, tzinfo=UTC)),
        ("2026-10-21T13:00:00+03:00", datetime(2026, 10, 21, 10, tzinfo=UTC)),
        ("2026-10-21T10:00:00", datetime(2026, 10, 21, 10, tzinfo=UTC)),  # без пояса — UTC
        ("вчера", None),
        (7, None),
    ],
)
def test_the_refusal_moment_is_read_or_ignored(written: object, read: datetime | None) -> None:
    """Момент отказа правили руками: не разобрать — паузы нет, и срок прохода прежний (отказ
    поиска не роняет задачу на срок); без пояса — UTC, как пишет сам проход."""
    assert segment_paid.refused_at({"contacts": {segment_paid.REFUSED_AT: written}}) == read
    assert segment_paid.refused_at({"contacts": "правили руками"}) is None
    assert segment_paid.refused_at({}) is None
