"""Поиск адреса сам после «пишем» у бизнеса ниши и добор задачи поиска (29.10.2026).

Тест-группа: из 180 бизнесов ниши, решённых «пишем», письма собрались трём — адрес был у восьми,
а поиск после «пишем» никто не ставил (`api/advertisers/routes._search_after_yes`). Решения идут
пачкой, задача поиска одна — решённых за время прохода берёт её добор (`crawl/address_search.py`).
Письмо бизнеса, получившего адрес, встаёт в ту же очередь, которую уходит проход автоотправки.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.search import SearchReport
from backend.features.core.domain import (
    ContactSource,
    ContactStatus,
    MessageStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.crawl import address_search, niche
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.letters import autosend
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.transport import NullTransport
from backend.shared.queue import CONTACTS_JOB
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_niche_letters as niche_letters
from tests.conftest import bearer
from tests.test_letters_advertisers import build_offers, offers, priced_donor, stage_two_sender
from tests.test_niche_advertisers import _domain, _run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
ROUTES = "backend.api.advertisers.routes"


class FakeBase:
    """Очередь рекламодателей: ждущие — список, который «решения» дописывают по ходу задачи.
    Пройденных он не вычёркивает — как правило попыток у сайта, который не ответил."""

    stage = Stage.ADVERTISERS

    def __init__(self, waiting: list[str]) -> None:
        self.waiting = waiting

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        return self.waiting[:limit]

    async def save(self, results: Sequence[LadderResult]) -> int:
        return len(results)

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return set()


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _passes(
    monkeypatch: pytest.MonkeyPatch, base: FakeBase, later: dict[int, list[str]]
) -> list[str]:
    """Подменить очередь и лестницу задачи; `later` — решения, пришедшие за N-й круг."""
    walked: list[str] = []
    rounds = [0]

    async def search(_session: Any, *, limit: int, queue: Any, **_: Any) -> SearchReport:
        hosts = await queue.pending_hosts(limit=limit)
        walked.extend(hosts)
        rounds[0] += 1
        base.waiting.extend(later.get(rounds[0], []))
        return SearchReport(
            pending=len(hosts),
            walked=len(hosts),
            saved=len(hosts),
            counters={"pages_entered": len(hosts)},
            notes=["Платный сервис: остаток квоты 10 поисков."],
        )

    monkeypatch.setattr(address_search, "AdvertiserContactRepository", lambda _session: base)
    monkeypatch.setattr(address_search, "search_contacts", search)
    return walked


async def _followup(session: FakeSession, limit: int = 1000) -> SearchReport:
    return await address_search.search_with_followup(
        session,  # type: ignore[arg-type]  # подделка: задаче нужен только commit
        limit=limit,
        use_browser=False,
        paid_first=False,
    )


class TestFollowup:
    async def test_decisions_during_the_pass_go_into_the_next_round(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = FakeBase(["first.example.com"])
        walked = _passes(monkeypatch, base, {1: ["second.example.com", "third.example.com"]})
        session = FakeSession()

        report = await _followup(session)

        assert walked == ["first.example.com", "second.example.com", "third.example.com"]
        assert (report.pending, report.walked, report.saved) == (3, 3, 3)
        assert report.counters == {"pages_entered": 3}
        assert report.notes == ["Платный сервис: остаток квоты 10 поисков."]
        assert session.commits == 3  # два круга с доменами и пустой — каждый записан сам

    async def test_a_site_is_walked_once_per_job_even_if_the_queue_offers_it_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = FakeBase(["silent.example.com"])
        walked = _passes(monkeypatch, base, {})

        report = await _followup(FakeSession())

        assert walked == ["silent.example.com"]
        assert report.walked == 1

    async def test_the_job_limit_holds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        base = FakeBase(["a.example.com", "b.example.com", "c.example.com"])
        walked = _passes(monkeypatch, base, {})

        report = await _followup(FakeSession(), limit=2)

        assert walked == ["a.example.com", "b.example.com"]
        assert report.walked == 2

    def test_merged_report_sums_and_keeps_the_last_manual_count(self) -> None:
        one = SearchReport(pending=2, walked=2, saved=1, counters={"a": 1}, notes=["x"])
        two = SearchReport(
            pending=1,
            walked=1,
            saved=1,
            counters={"a": 2, "b": 1},
            manual_queue_left=4,
            notes=["x"],
        )

        merged = address_search.merged(one, two)

        assert (merged.pending, merged.walked, merged.saved) == (3, 3, 2)
        assert merged.counters == {"a": 3, "b": 1}
        assert (merged.manual_queue_left, merged.notes) == (4, ["x"])


class FakeQueue:
    """Очередь, которая ничего не выполняет: проверяется, что в неё положили."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.calls.append((job, args))
        return type("Job", (), {"id": "job-адреса"})()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr(f"{ROUTES}.contacts_queue", lambda: fake)
    monkeypatch.setattr(f"{ROUTES}.remember_contacts_job", lambda _job, **_k: None)
    monkeypatch.setattr(f"{ROUTES}.contacts_job_id", lambda **_k: None)
    return fake


async def _niche_business(session: AsyncSession, host: str = "bookie.example.com") -> int:
    domain = await _domain(session, host, judge=niche.SELLS_OWN)
    run_id = await _run(session, [host])
    await niche.collect(session, run_id)
    advertiser = await session.scalar(
        select(AdvertiserModel).where(AdvertiserModel.domain_id == domain.id)
    )
    assert advertiser is not None
    await session.commit()
    return advertiser.id


async def _decide(client: AsyncClient, token: str, advertiser_id: int, *, write: bool) -> None:
    said = await client.post(
        f"/api/advertisers/niche/{advertiser_id}/decide",
        json={"write": write},
        headers=bearer(token),
    )
    assert said.status_code == 200, said.text


class TestSearchAfterYes:
    async def test_yes_starts_the_address_search(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        queue: FakeQueue,
    ) -> None:
        advertiser_id = await _niche_business(session)
        await make_user("админ@site.com", role=UserRole.ADMIN)

        await _decide(client, await sign_in("админ@site.com"), advertiser_id, write=True)

        assert queue.calls == [(CONTACTS_JOB, (1000, False, False, None, "advertisers"))]

    async def test_no_starts_nothing(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        queue: FakeQueue,
    ) -> None:
        advertiser_id = await _niche_business(session)
        await make_user("админ@site.com", role=UserRole.ADMIN)

        await _decide(client, await sign_in("админ@site.com"), advertiser_id, write=False)

        assert queue.calls == []

    async def test_without_the_run_right_the_search_waits_for_the_button(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        queue: FakeQueue,
    ) -> None:
        advertiser_id = await _niche_business(session)
        await make_user("ревьюер@site.com", permissions={"run": False})

        await _decide(client, await sign_in("ревьюер@site.com"), advertiser_id, write=True)

        assert queue.calls == []

    async def test_a_running_search_is_not_doubled(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(f"{ROUTES}.contacts_job_id", lambda **_k: "job-идёт")
        monkeypatch.setattr(f"{ROUTES}.job_alive", lambda _job: True)
        advertiser_id = await _niche_business(session)
        await make_user("админ@site.com", role=UserRole.ADMIN)

        await _decide(client, await sign_in("админ@site.com"), advertiser_id, write=True)

        assert queue.calls == []  # решённого возьмёт добор идущей задачи

    async def test_a_business_with_an_address_starts_nothing(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        queue: FakeQueue,
    ) -> None:
        advertiser_id = await _niche_business(session)
        advertiser = await session.get(AdvertiserModel, advertiser_id)
        assert advertiser is not None
        session.add(
            ContactModel(
                domain_id=advertiser.domain_id,
                email="partners@bookie.example.com",
                source=ContactSource.PAGE,
            )
        )
        await session.commit()
        await make_user("админ@site.com", role=UserRole.ADMIN)

        await _decide(client, await sign_in("админ@site.com"), advertiser_id, write=True)

        assert queue.calls == []


async def test_niche_funnel_says_who_waits_for_an_address_and_who_has_none(
    session: AsyncSession,
) -> None:
    waits = await _niche_business(session, "waits.example.com")
    tried = await _niche_business(session, "tried.example.com")
    for advertiser_id in (waits, tried):
        await niche.decide(session, advertiser_id, write=True, by="anthony@site.test")
    advertiser = await session.get(AdvertiserModel, tried)
    assert advertiser is not None
    advertiser.contact_status = ContactStatus.NOT_FOUND
    advertiser.contact_attempted_at = datetime.now(UTC)
    await session.flush()

    funnel = await NicheRecipients(session).niche_report()

    assert (funnel["решено «пишем»"], funnel["из них с адресом"]) == (2, 0)
    assert (funnel["ждут поиска адреса"], funnel["адреса не нашлось"]) == (1, 1)


async def test_letters_tabs_count_their_own_waiting(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    queue: FakeQueue,
) -> None:
    """Строка «ждут адреса» на вкладке писем — числом своей вкладки, «К письму» — всех."""
    business = await _niche_business(session, "waits.example.com")
    await niche.decide(session, business, write=True, by="anthony@site.test")
    linked = await _domain(session, "linked.example.com")
    session.add(AdvertiserModel(domain_id=linked.id, source=niche.LINKS))
    await session.commit()
    await make_user("админ@site.com", role=UserRole.ADMIN)
    token = await sign_in("админ@site.com")

    counted: dict[str | None, int] = {}
    for audience in ("niche", "links", None):
        query = "" if audience is None else f"?audience={audience}"
        said = await client.get(f"/api/advertisers/contacts{query}", headers=bearer(token))
        assert said.status_code == 200, said.text
        counted[audience] = said.json()["pending"]

    assert counted == {"niche": 1, "links": 1, None: 2}


class TestTheLetterJoinsTheSelfSendingQueue:
    """Адрес нашёлся, пока очередь бизнесов ниши уходит сама (`letters/autosend.py`): письмо
    следующей сборки встаёт в ту же очередь — этап и аудиторию, — и его отдаёт проход."""

    async def test_the_pass_sends_the_letter_of_a_business_that_got_its_address(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Окно получателя — круглые сутки: проверка не об окне, а о том, чья это очередь.
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(7)))
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time.min)
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time.max)
        await priced_donor(session, niche_letters.DONOR)
        await niche_letters._business(session, contact=False)
        await stage_two_sender(session)
        now = datetime.now(UTC)
        mode, fresh = await autosend.start(
            session, Stage.ADVERTISERS, niche.NICHE, by=None, now=now
        )
        assert fresh
        waiting = AdvertiserContactRepository(session)
        assert await waiting.pending_count(niche=True) == 1

        # Поиск нашёл адрес — строка `contacts` домена, как её пишет лестница.
        domain = await session.scalar(
            select(DomainModel).where(DomainModel.host == niche_letters.BOOKIE)
        )
        assert domain is not None
        session.add(
            ContactModel(
                domain_id=domain.id,
                email=f"partners@{niche_letters.BOOKIE}",
                source=ContactSource.PAGE,
            )
        )
        await session.flush()
        assert await waiting.pending_count(niche=True) == 0
        await build_offers(session, audience=niche.NICHE, campaign_name="Ниша")

        passed = await autosend.run(session, NullTransport(), mode, now=now)

        assert passed.state.startswith("ушло 1")
        [letter] = await offers(session)
        assert letter.status == MessageStatus.SENT
        assert mode.stopped_at is None
