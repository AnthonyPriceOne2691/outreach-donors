"""Письма бизнесам ниши: кому, о чём, и чем их цепочка отличается от оффера по ссылке.

Бизнес ниши (`crawl/niche.py`) получает письмо только после «пишем» человека
и только когда есть о чём писать: принятый донор того же прогона со свежей
ценой — пример нашей площадки. Ссылки, которую «мы видели», у него нет,
поэтому ни в оффере, ни в добивках её быть не должно. Проверяется на
настоящей базе.

Очередь Этапа 2 делят две аудитории, и пачка «Отправить очередь · N» —
одной из них: кнопка на вкладке «Бизнесам ниши» не отправляет письма по
найденной ссылке, а на «Рекламодателям» — письма бизнесам ниши.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import ContactSource, MessageStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.donors.verdict import Thresholds
from backend.features.letters import batch, compose, template, unknown_outcome
from backend.features.letters.draft import LetterConflictError
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.repository import LetterRepository
from backend.features.letters.transport import NullTransport
from backend.features.runs.repository import RunRepository
from backend.shared.queue import SEND_QUEUE_JOB
from backend.workers import send_jobs
from httpx import AsyncClient
from rq import Queue
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letter_draft import FakeQueue
from tests.test_letters_advertisers import (
    build_offers,
    make_advertiser,
    offers,
    priced_donor,
    stage_two_sender,
)

BOOKIE = "bookie.example.test"
BRAND = "brand.example.test"
DONOR = "donor.example.test"

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _run(session: AsyncSession) -> int:
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
        keywords=["betting tips"],
        country="de",
        candidates={"hosts": [BOOKIE, DONOR], "found_by": {BOOKIE: ["sports betting"]}},
    )
    session.add(run)
    await session.flush()
    return run.id


async def _business(session: AsyncSession, *, write: bool = True, contact: bool = True) -> int:
    """Бизнес ниши из прогона, решение человека и адрес."""
    domain = DomainModel(host=BOOKIE, site_intent=niche.SELLS_OWN)
    session.add(domain)
    await session.flush()
    run_id = await _run(session)
    await niche.collect(session, run_id)
    advertiser = await session.scalar(
        select(AdvertiserModel).where(AdvertiserModel.domain_id == domain.id)
    )
    assert advertiser is not None
    if write:
        await niche.decide(session, advertiser.id, write=True, by="anthony@site.test")
    if contact:
        session.add(
            ContactModel(domain_id=domain.id, email=f"partners@{BOOKIE}", source=ContactSource.PAGE)
        )
    await session.flush()
    return run_id


class TestTemplates:
    def test_niche_offer_keeps_example_and_topic_out_of_the_model(self) -> None:
        offer = template.for_stage(Stage.ADVERTISERS, niche.NICHE)
        fixed = " ".join(zone.text for zone in offer.of_kind(template.ZoneKind.FIXED))
        assert "{{example_host}}" in fixed
        assert "{{niche}}" in fixed
        assert "{{page_url}}" not in offer.body  # ссылки, которую «видели», нет

    @pytest.mark.parametrize("step", [1, 2])
    def test_niche_followups_do_not_mention_their_placement(self, step: int) -> None:
        followup = template.followup(step, Stage.ADVERTISERS, niche.NICHE)
        links = template.followup(step, Stage.ADVERTISERS)
        assert "placement of yours" not in followup.body
        assert followup.body != links.body


class TestWhoAndWhat:
    async def test_decided_business_gets_an_offer_about_our_site(
        self, session: AsyncSession
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)

        recipients = NicheRecipients(session)
        [candidate] = await recipients.niche_candidates(limit=10)
        funnel = await recipients.niche_report()

        assert candidate.host == BOOKIE
        assert candidate.link is None
        assert candidate.niche is not None
        assert candidate.niche.example_host == DONOR
        assert candidate.niche.niche == "sports betting"  # ключ, по которому он нашёлся
        assert candidate.niche.donor_geo == "de"
        assert (funnel["решено «пишем»"], funnel["ещё не писали"]) == (1, 1)

    @pytest.mark.parametrize(
        ("write", "price_days", "why"),
        [(False, 10, "не решили «пишем»"), (True, None, "у примера площадки нет цены")],
    )
    async def test_no_offer_without_decision_or_priced_example(
        self, session: AsyncSession, write: bool, price_days: int | None, why: str
    ) -> None:
        await priced_donor(session, DONOR, priced_days_ago=price_days)
        await _business(session, write=write)

        assert await NicheRecipients(session).niche_candidates(limit=10) == [], why


class TestBuild:
    async def test_niche_campaign_writes_the_niche_offer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)

        report = await build_offers(session, audience=niche.NICHE, campaign_name="Ниша")

        assert report.prepared == 1  # type: ignore[attr-defined]
        assert report.funnel["решено «пишем»"] == 1  # type: ignore[attr-defined]
        [letter] = await offers(session)
        assert DONOR in (letter.body or "")
        assert "sports betting" in (letter.body or "")
        assert "Germany" in (letter.body or "")
        campaign = await session.get(CampaignModel, letter.campaign_id)
        assert campaign is not None
        assert campaign.audience == niche.NICHE

    async def test_links_campaign_does_not_pick_niche_businesses(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)

        report = await build_offers(session)

        assert report.prepared == 0  # type: ignore[attr-defined]

    async def test_same_name_for_another_audience_is_refused(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)
        await build_offers(session, campaign_name="Октябрь")

        with pytest.raises(
            LetterConflictError, match="уже идёт рекламодателям по найденной ссылке"
        ):
            await build_offers(session, campaign_name="Октябрь", audience=niche.NICHE)


async def test_offer_is_recomputed_for_a_queued_letter(session: AsyncSession) -> None:
    """Правка письма в очереди меряется от оффера сейчас; не собрать — `None`."""
    donor = await priced_donor(session, DONOR)
    await _business(session)
    bookie = await session.scalar(select(DomainModel).where(DomainModel.host == BOOKIE))
    assert bookie is not None
    recipients = NicheRecipients(session)

    offer = await recipients.offer_of(bookie.id)
    record = await session.scalar(select(DonorModel).where(DonorModel.domain_id == donor.id))
    assert record is not None
    record.last_price_at = datetime(2020, 1, 1, tzinfo=UTC)  # цена протухла
    await session.flush()

    assert offer is not None
    assert offer.example_host == DONOR
    assert await recipients.offer_of(bookie.id) is None


class TestScreen:
    @pytest.fixture
    async def token(self, make_user: MakeUser, sign_in: SignIn) -> str:
        await make_user("админ@site.com", role=UserRole.ADMIN)
        return await sign_in("админ@site.com")

    async def _queued(self, session: AsyncSession) -> int:
        await priced_donor(session, DONOR)
        await _business(session)
        await build_offers(session, audience=niche.NICHE, campaign_name="Ниша")
        await session.commit()
        [letter] = await offers(session)
        return letter.id

    async def test_niche_queue_has_its_own_letters_text_funnel_and_reminders(
        self, client: AsyncClient, token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        """Оффер по ссылке и оффер бизнесу ниши читаются разными глазами —
        в одной очереди человек согласовал бы не то."""
        await self._queued(session)

        shown = (
            await client.get("/api/letters?stage=advertisers&audience=niche", headers=bearer(token))
        ).json()
        links = (await client.get("/api/letters?stage=advertisers", headers=bearer(token))).json()

        assert links["letters"] == []
        assert shown["audience"] == "niche"
        assert shown["letter_default"]["subject"] == "Sponsored articles on {{niche}} sites"
        assert shown["funnel"]["решено «пишем»"] == 1
        assert shown["funnel"]["ещё не писали"] == 0  # письмо уже в очереди
        [card] = shown["letters"]
        assert "sites in your field" in card["followups"][0]["body"]

    async def test_audience_travels_to_the_job_and_niche_is_stage_two_only(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue = FakeQueue()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: queue)

        built = await client.post(
            "/api/letters/build",
            json={"campaign": "Ниша", "stage": "advertisers", "audience": "niche"},
            headers=bearer(token),
        )
        refused = await client.post(
            "/api/letters/build",
            json={"campaign": "Ниша", "stage": "donors", "audience": "niche"},
            headers=bearer(token),
        )

        assert built.status_code == 200, built.text
        assert queue.kwargs[0]["audience"] == "niche"
        assert refused.status_code == 422
        assert "только на Этапе 2" in refused.text
        assert len(queue.kwargs) == 1

    async def test_same_name_for_another_audience_is_refused_before_the_job(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        filled_legal: None,
    ) -> None:
        """Одноимённая рассылка другой аудитории — 409 у формы: задача сборки отказала бы
        через минуты, после повторов."""
        await priced_donor(session, DONOR)
        await _business(session)
        await build_offers(session, campaign_name="Октябрь")
        await session.commit()
        queue = FakeQueue()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: queue)

        refused = await client.post(
            "/api/letters/build",
            json={"campaign": "Октябрь", "stage": "advertisers", "audience": "niche"},
            headers=bearer(token),
        )

        assert refused.status_code == 409, refused.text
        assert "назовите новую" in refused.json()["detail"]
        assert queue.kwargs == []

    @pytest.mark.parametrize("path", ["/api/letters", "/api/letters/unknown"])
    async def test_niche_view_is_stage_two_only(
        self, client: AsyncClient, token: str, path: str
    ) -> None:
        shown = await client.get(f"{path}?stage=donors&audience=niche", headers=bearer(token))

        assert shown.status_code == 422
        assert "только на Этапе 2" in shown.text

    async def test_edit_is_measured_against_the_niche_offer_until_it_is_gone(
        self, client: AsyncClient, token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        letter_id = await self._queued(session)
        offer = compose.NicheOffer(example_host=DONOR, niche="sports betting", donor_geo="de")
        plain = compose.render(
            template.for_stage(Stage.ADVERTISERS, niche.NICHE),
            compose.values_for(host=BOOKIE, niche=offer),
        )

        edited = await client.patch(
            f"/api/letters/{letter_id}",
            json={"subject": plain.subject, "body": plain.body},
            headers=bearer(token),
        )
        record = await session.scalar(
            select(DonorModel).join(DomainModel).where(DomainModel.host == DONOR)
        )
        assert record is not None
        record.last_price_at = datetime(2020, 1, 1, tzinfo=UTC)
        await session.commit()
        gone = await client.patch(
            f"/api/letters/{letter_id}",
            json={"subject": plain.subject, "body": plain.body},
            headers=bearer(token),
        )

        assert edited.status_code == 200, edited.text
        assert edited.json()["uniqueness"] == 0.0
        assert gone.status_code == 409
        assert "больше не собрать" in gone.json()["detail"]


class TestNotWritingAfterTheBuild:
    """Отправка решение человека не перепроверяет: «не пишем» после сборки снимает письма."""

    async def test_not_writing_stops_the_niche_letter_and_its_reminders(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        by_link, to_niche = await _both_queued(session)
        advertiser = await session.scalar(
            select(AdvertiserModel).where(AdvertiserModel.domain_id == to_niche.domain_id)
        )
        assert advertiser is not None
        # Тот же домен — и в рассылке по ссылке: её письмо решает своё решение.
        by_link.domain_id = to_niche.domain_id
        await session.flush()

        decided = await niche.decide(session, advertiser.id, write=False, by="anthony@site.test")

        assert decided.stopped == 1
        assert await _statuses(session, to_niche, by_link) == [
            MessageStatus.STOPPED,
            MessageStatus.QUEUED,
        ]

    async def test_not_writing_after_the_first_letter_clears_the_reminders(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        _, to_niche = await _both_queued(session)
        to_niche.status = MessageStatus.SENT
        to_niche.next_action_at = datetime.now(UTC) + timedelta(days=3)
        await session.flush()
        advertiser = await session.scalar(
            select(AdvertiserModel).where(AdvertiserModel.domain_id == to_niche.domain_id)
        )
        assert advertiser is not None

        decided = await niche.decide(session, advertiser.id, write=False, by="anthony@site.test")

        await session.refresh(to_niche)
        assert decided.stopped == 1
        assert (to_niche.status, to_niche.next_action_at) == (MessageStatus.SENT, None)

    async def test_writing_stops_nothing(self, session: AsyncSession, filled_legal: None) -> None:
        _, to_niche = await _both_queued(session)
        advertiser = await session.scalar(
            select(AdvertiserModel).where(AdvertiserModel.domain_id == to_niche.domain_id)
        )
        assert advertiser is not None

        decided = await niche.decide(session, advertiser.id, write=True, by="anthony@site.test")

        assert decided.stopped == 0
        assert await _statuses(session, to_niche) == [MessageStatus.QUEUED]


async def _both_queued(session: AsyncSession) -> tuple[MessageModel, MessageModel]:
    """В очереди Этапа 2 — оффер по найденной ссылке и оффер бизнесу ниши, ящик Этапа 2."""
    await priced_donor(session, DONOR)
    await make_advertiser(session, BRAND)
    await build_offers(session, campaign_name="По ссылке")
    await _business(session)
    await build_offers(session, audience=niche.NICHE, campaign_name="Ниша")
    await stage_two_sender(session)
    await session.commit()
    by_link, to_niche = await offers(session)
    return by_link, to_niche


async def _statuses(session: AsyncSession, *letters: MessageModel) -> list[MessageStatus]:
    for letter in letters:
        await session.refresh(letter)
    return [letter.status for letter in letters]


class TestBatchOfOneAudience:
    """Пачка одной аудитории не берёт письма другой — ни в отправку, ни в «осталось»."""

    async def test_the_niche_batch_leaves_letters_by_link_in_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        by_link, to_niche = await _both_queued(session)

        report = await batch.send_queue(
            session, NullTransport(), stage=Stage.ADVERTISERS, audience=niche.NICHE
        )

        assert (report.sent, report.left, report.stopped) == (1, 0, None)
        assert await _statuses(session, to_niche, by_link) == [
            MessageStatus.SENT,
            MessageStatus.QUEUED,
        ]

    async def test_the_advertisers_batch_leaves_niche_letters_in_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Прежний вызов — без аудитории, как ставят задачу «Рекламодателям» и задачи,
        поставленные до бизнесов ниши: письмо ниши он не трогает."""
        by_link, to_niche = await _both_queued(session)

        report = await batch.send_queue(session, NullTransport(), stage=Stage.ADVERTISERS)

        assert (report.sent, report.left, report.stopped) == (1, 0, None)
        assert await _statuses(session, by_link, to_niche) == [
            MessageStatus.SENT,
            MessageStatus.QUEUED,
        ]

    async def test_unknown_outcome_is_shown_on_the_tab_of_its_audience(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо, на котором встала пачка ниши, решают на вкладке ниши."""
        by_link, to_niche = await _both_queued(session)
        for letter in (by_link, to_niche):
            letter.status = MessageStatus.SENDING
        await session.flush()
        later = datetime.now(UTC) + timedelta(hours=1)

        def ids(found: list[unknown_outcome.StuckLetter]) -> list[int]:
            return [letter.message.id for letter in found]

        stage = Stage.ADVERTISERS
        assert ids(await unknown_outcome.stuck(session, stage=stage, now=later)) == [
            by_link.id,
            to_niche.id,
        ]
        assert ids(
            await unknown_outcome.stuck(session, stage=stage, audience=niche.NICHE, now=later)
        ) == [to_niche.id]
        assert ids(
            await unknown_outcome.stuck(session, stage=stage, audience=niche.LINKS, now=later)
        ) == [by_link.id]


class TestOnlyStageTwoIsSplit:
    async def test_another_stage_counts_and_shows_its_whole_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Аудитории делят очередь только у Этапа 2. Рассылка другого этапа с чужой
        аудиторией (база её не запрещает) — в очереди своего этапа и в его «исходе
        неизвестен»: иначе вкладка звала бы «Отправить очередь · N», а пачка отвечала
        «ушло 0» (замечание ревью продаж, 09.10.2026)."""
        _, to_niche = await _both_queued(session)
        campaign = await session.get(CampaignModel, to_niche.campaign_id)
        assert campaign is not None
        campaign.stage = Stage.SALES
        await session.flush()

        waiting = await LetterRepository(session).queued_count(
            stage=Stage.SALES, audience=niche.LINKS
        )
        to_niche.status = MessageStatus.SENDING
        await session.flush()
        stuck = await unknown_outcome.stuck(
            session,
            stage=Stage.SALES,
            audience=niche.LINKS,
            now=datetime.now(UTC) + timedelta(hours=1),
        )

        assert waiting == 1
        assert [letter.message.id for letter in stuck] == [to_niche.id]


class _Jobs:
    """Очередь задач, которая помнит доводы целиком — и позиционные, и ключами, — какими
    их получит задача: параметры постановки (`job_id`, `unique`, `result_ttl`) снимает тот же
    разбор, что у `rq.Queue.enqueue`, — функции задачи они не передаются."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, job: str, *args: Any, **kwargs: Any) -> object:
        parsed = Queue.parse_args(job, *args, **kwargs)
        self.calls.append((job, tuple(parsed.args or ()), dict(parsed.kwargs or {})))
        return type("Job", (), {"id": "job-ниша"})()


class TestTheButtonOfTheNicheTab:
    @pytest.fixture
    async def admin(self, make_user: MakeUser, sign_in: SignIn) -> tuple[UserModel, str]:
        user = await make_user("админ@site.com", role=UserRole.ADMIN)
        return user, await sign_in("админ@site.com")

    async def test_each_tab_counts_and_sends_its_own_letters(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        admin: tuple[UserModel, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """«Отправить очередь · N» на вкладке ниши: N — письма ниши, задача — с их
        аудиторией и с доводами, которые задача принимает."""
        user, token = admin
        await _both_queued(session)
        jobs = _Jobs()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs)
        await make_advertiser(session, "second-brand.example.test")
        await build_offers(session, campaign_name="По ссылке")
        await session.commit()

        tab = (
            await client.get("/api/letters?stage=advertisers&audience=niche", headers=bearer(token))
        ).json()
        links = (await client.get("/api/letters?stage=advertisers", headers=bearer(token))).json()
        sent = await client.post(
            "/api/letters/send-queue",
            json={"stage": "advertisers", "audience": "niche"},
            headers=bearer(token),
        )

        assert (tab["queued_total"], links["queued_total"]) == (1, 2)
        assert sent.status_code == 200, sent.text
        assert sent.json() == {"job_id": "job-ниша", "queued": 1}
        [(path, args, kwargs)] = jobs.calls
        assert path == SEND_QUEUE_JOB
        bound = inspect.signature(send_jobs.send_letter_queue).bind(*args, **kwargs)
        assert bound.arguments == {
            "stage": "advertisers",
            "author_id": user.id,
            "audience": "niche",
        }

    async def test_batch_by_link_is_understood_by_the_worker_before_audiences(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        admin: tuple[UserModel, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Пачка «Рекламодателям» — задача без аудитории: нажатую в секунды выкатки
        возьмёт и воркер прежней версии, у которого аудитории ещё нет."""
        user, token = admin
        await _both_queued(session)
        jobs = _Jobs()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs)

        def before_audiences(stage: str, author_id: int | None = None) -> None:
            """Задача пачки до бизнесов ниши (#237)."""

        sent = await client.post(
            "/api/letters/send-queue", json={"stage": "advertisers"}, headers=bearer(token)
        )

        assert sent.status_code == 200, sent.text
        [(_, args, kwargs)] = jobs.calls
        bound = inspect.signature(before_audiences).bind(*args, **kwargs)
        assert bound.arguments == {"stage": "advertisers", "author_id": user.id}

    async def test_niche_batch_is_stage_two_only(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        jobs = _Jobs()
        monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: jobs)
        _, token = admin

        refused = await client.post(
            "/api/letters/send-queue",
            json={"stage": "donors", "audience": "niche"},
            headers=bearer(token),
        )

        assert refused.status_code == 422
        assert "только на Этапе 2" in refused.text
        assert jobs.calls == []


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / (
        "backend/migrations/versions/9c4792694f6a_campaign_audience.py"
    )
    spec = importlib.util.spec_from_file_location("campaign_audience_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audience_column(connection: Connection) -> tuple[bool, bool]:
    def exists() -> bool:
        found = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'campaigns' AND column_name = 'audience'"
            )
        ).first()
        return found is not None

    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = exists()
        migration.upgrade()
    return down, exists()


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_audience_column) == (False, True)
