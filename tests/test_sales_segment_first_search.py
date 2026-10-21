"""Первый поиск адреса принятым с платной ступенью (`segment_run._addresses`, `segment_paid.first_search`):
пачками с резервом, в пределах сметы, и падение посреди пачки не теряет оплаченного и не платит
за те же сайты повтором задачи.

До правки первый поиск шёл одним вызовом лестницы без резерва: итог (и запросы Hunter) ложился в
отчёт прогона только в конце, пачка, упавшая до своего коммита, не оставляла ни исхода, ни строки
журнала, а повтор задачи спрашивал те же сайты снова — оплаченное пропадало из счёта, и остаток
сметы врал вверх. Здесь: остаток сметы после сбоя — за вычетом резерва упавшей пачки; пачки до
сбоя — уже в счёте; повтор задачи сайты упавшей пачки не берёт (исход «спрошен, ответа нет» —
повтор по сроку); сверх сметы первый поиск не платит, и смета, выбранная прошлым проходом, тоже
держит; без сбоя исходы — настоящие, а «не спросили» платной ступени — её словами.

Сайты — `httpx.MockTransport` (`test_sales_segment_again.Web`): главная без адреса — платная
ступень по ним зовётся. DNS — «почта есть»; платная ступень — подделка со счётом обращений; модель
и выдача — подделки прогона (`test_sales_segment_run`), выдача уже куплена. Ниши, рынки и зоны
разные, домены выдуманы.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.provider import PaidUnavailable
from backend.features.contacts.search import SearchReport
from backend.features.core.domain import ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.sales import cleaning, segment_again
from backend.features.sales.segment_models import SalesSegmentCandidateModel as Candidate
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_queue import SegmentContactQueue, paid_left
from backend.features.sales.segment_spend import pending, searched, unrecorded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_contacts_no_answer import Paid
from tests.test_sales_segment_again import LEAST, Web, _accepted, _paid_ready, _run, _sites
from tests.test_sales_segment_run import Model, go, hypothesis, make_run, serp

#: Сайты без адреса на главной — платная ступень по ним зовётся: столярная мастерская (.at),
#: пекарня (.com.mx), гостиница (.jp), сантехник (.co.nz).
PLAIN = [
    "tischlerei-sample.at",
    "panaderia-sample.com.mx",
    "onsen-ryokan-sample.jp",
    "plumber-sample.co.nz",
]


@pytest.fixture(autouse=True)
def _network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS — у всех доменов почта есть."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(cleaning, "mail_route", route)


async def _bought(
    session: AsyncSession, hosts: Sequence[str], *, searches: int, spent: int = 0
) -> SalesSegmentRunModel:
    """Прогон с купленной выдачей и принятыми сайтами — дальше только адреса и лиды; `searches` —
    платных поисков в смете запуска, `spent` — уже принятых провайдером прошлым проходом."""
    run = await make_run(session, await hypothesis(session))
    run.estimate = {**run.estimate, "hunter_searches": searches}
    run.report = {"serp": {"hosts": len(hosts)}}
    if spent:
        run.report["contacts"] = {"walked": spent, "counters": {"provider_entered": spent}}
    await session.commit()
    for host in hosts:
        await _accepted(session, run, host)
    return run


async def _first_search(session: AsyncSession, run: SalesSegmentRunModel) -> dict[str, Any]:
    """Проход прогона: выдача куплена, судить некого — только адреса и лиды («Найти адреса
    принятым» и повтор задачи)."""
    return await go(session, run, serp(), Model({}))


def _dies_on_save(monkeypatch: pytest.MonkeyPatch, *, on: int = 1) -> list[int]:
    """Воркер падает, когда пачка сохраняет исход в `on`-й раз: Hunter уже ответил, а ни исхода,
    ни строки журнала пачки нет — её коммит не случился."""
    saving = SegmentContactQueue.save
    saves: list[int] = []

    async def save(queue: SegmentContactQueue, results: Sequence[Any]) -> int:
        saves.append(len(results))
        if len(saves) == on:
            raise ConnectionError("воркер упал посреди пачки: Hunter ответил, исход не записан")
        return await saving(queue, results)

    monkeypatch.setattr(SegmentContactQueue, "save", save)
    return saves


async def _outcomes(
    session: AsyncSession, run: SalesSegmentRunModel
) -> dict[str, tuple[ContactStatus | None, int]]:
    rows = await session.execute(
        select(DomainModel.host, Candidate.contact_status, Candidate.contact_tries)
        .join(Candidate, Candidate.domain_id == DomainModel.id)
        .where(Candidate.run_id == run.id)
    )
    return {host: (status, tries) for host, status, tries in rows.tuples().all()}


# --- падение посреди пачки ---------------------------------------------------------------------


async def test_a_crash_mid_batch_counts_the_batch_as_spent(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смета 10, пачка из трёх сайтов, Hunter принял три запроса, воркер упал до записи. До
    правки отчёт не знал ни запросов, ни пачки — остаток сметы 10. Теперь резерв пачки стоит в
    отчёте своим коммитом до запросов: остаток — 7."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[:3], searches=10)
    _dies_on_save(monkeypatch)

    with pytest.raises(ConnectionError):
        await _first_search(session, run)

    crashed = await _run(session, run)
    assert len(paid.calls) == 3
    assert (pending(crashed.report), paid_left(crashed.report, crashed.estimate)) == (3, 7)


async def test_the_job_retry_does_not_pay_for_the_crashed_batch_again(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повтор задачи после того же сбоя: до правки он спрашивал те же три сайта снова — шесть
    запросов при трёх в счёте. Теперь сайты упавшей пачки помечены «спрошен, ответа нет» тем же
    коммитом, что резерв: повтор задачи их не берёт, резерв сведён в «принято без записи» (остаток
    7), а сами сайты — в обычном повторе по сроку, не раньше `SALES_SEGMENT_AGAIN_MIN`."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[:3], searches=10)
    saving = SegmentContactQueue.save
    _dies_on_save(monkeypatch)
    crashed_at = datetime.now(UTC)
    with pytest.raises(ConnectionError):
        await _first_search(session, run)
    monkeypatch.setattr(SegmentContactQueue, "save", saving)

    await _first_search(session, run)  # повтор задачи rq

    after = await _run(session, run)
    assert len(paid.calls) == 3
    assert (pending(after.report), unrecorded(after.report)) == (0, 3)
    assert paid_left(after.report, after.estimate) == 7
    assert await _outcomes(session, run) == dict.fromkeys(PLAIN[:3], (ContactStatus.NO_ANSWER, 0))
    term = await segment_again.next_at(session, run.id)
    assert term is not None
    assert term >= crashed_at + LEAST


async def test_batches_before_the_crash_are_already_in_the_count(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пачка по два сайта: первая прошла, вторая упала до записи. До правки запросы попадали в
    отчёт только в конце поиска, и обе пачки пропадали из счёта — остаток 10. Теперь итог каждой
    пачки — в отчёте своим коммитом: принято 2, в резерве 2, остаток 6."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    monkeypatch.setattr("backend.features.contacts.search.BATCH", 2)
    run = await _bought(session, PLAIN, searches=10)
    _dies_on_save(monkeypatch, on=2)

    with pytest.raises(ConnectionError):
        await _first_search(session, run)

    crashed = await _run(session, run)
    assert len(paid.calls) == 4
    assert (searched(crashed.report), pending(crashed.report)) == (2, 2)
    assert paid_left(crashed.report, crashed.estimate) == 6


# --- смета ---------------------------------------------------------------------------------------


async def test_the_first_search_pays_no_more_than_the_estimate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смета 2, сайтов, которым нужна платная ступень, — три: до правки первый поиск платил всем
    (смета — «до 2»). Теперь двум — платно, третьему — только бесплатные ступени: «не спросили»,
    его возьмёт «Повторить сейчас», если в смете появится остаток."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[:3], searches=2)

    await _first_search(session, run)

    after = await _run(session, run)
    assert paid.calls == PLAIN[:2]
    assert (await _outcomes(session, run))[PLAIN[2]] == (ContactStatus.PAID_OFF, 0)
    assert (searched(after.report), paid_left(after.report, after.estimate)) == (2, 0)


async def test_an_estimate_spent_by_an_earlier_pass_holds_the_next_one(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смета 2 выбрана прошлым проходом, человек принял ещё сайт и нажал «Найти адреса
    принятым»: до правки поиск звал платную ступень снова. Теперь — без неё: «не спросили»."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[3:], searches=2, spent=2)

    await _first_search(session, run)

    assert paid.calls == []
    assert await _outcomes(session, run) == {PLAIN[3]: (ContactStatus.PAID_OFF, 0)}


# --- без сбоя — как прежде ----------------------------------------------------------------------


async def test_without_a_crash_the_outcomes_are_real_and_the_count_exact(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пометка «спрошен, ответа нет» до запроса — только на случай сбоя: исход пачки её
    перезаписывает, резерв снят, запросы — в счёте ровно."""
    paid = Paid()
    _paid_ready(monkeypatch, paid)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[:3], searches=10)

    await _first_search(session, run)

    after = await _run(session, run)
    assert len(paid.calls) == 3
    assert {status for status, _ in (await _outcomes(session, run)).values()} == {
        ContactStatus.NOT_FOUND
    }
    assert (searched(after.report), pending(after.report), unrecorded(after.report)) == (3, 0, 0)


async def test_an_unready_paid_step_keeps_its_own_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Квота кончилась до поиска: пачки спрашивают платную ступень сами (`paid_step`), и сайты без
    адреса на страницах получают её «кончилась квота», а не безличное «не спросили»."""
    quota = PaidUnavailable(ContactStatus.NO_QUOTA, "квота платного сервиса исчерпана")

    async def spent(_http: httpx.AsyncClient, report: SearchReport) -> None:
        report.paid_missing = quota

    _paid_ready(monkeypatch, Paid())
    monkeypatch.setattr("backend.features.contacts.search.paid_step", spent)
    _sites(monkeypatch, Web())
    run = await _bought(session, PLAIN[:2], searches=10)

    await _first_search(session, run)

    assert await _outcomes(session, run) == dict.fromkeys(PLAIN[:2], (ContactStatus.NO_QUOTA, 0))
