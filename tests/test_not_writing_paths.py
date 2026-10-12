"""«Кому не пишем» — одно условие на всех путях и порог крупного сайта в версии (12.10.2026).

Проверка ветки нашла: сборка по прогону запиралась навсегда (контакты «не искали» у тех,
кому поиск больше не ищет), ждущие строки крупных можно было принять, зоны и платформы
на выходах не перепроверялись, а `refind` и обход Этапа 2 шли в обход условия. Граница
«DR > 80» жила числом в коде, а по требованиям все пороги — в настройках прогона.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.contacts.refind import NamedDomains
from backend.features.core.domain import MessageStatus, RunStatus, Stage, UserRole
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.crawl import targets
from backend.features.crawl.denylist import MAJOR_MEDIA
from backend.features.crawl.niche import NICHE
from backend.features.donors.standing import waiting
from backend.features.donors.verdict import Thresholds, big_site_reason, check_dr
from backend.features.letters import attempts, batch
from backend.features.letters.building import run_scope
from backend.features.letters.followups import send_due
from backend.features.letters.niche_recipients import NicheRecipients, run_hosts
from backend.features.letters.recipients import Recipients
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import NotWritingError, Sending
from backend.features.letters.transport import NullTransport
from backend.features.review.candidates import Decision, RunReview
from backend.features.runs.exclusions import (
    NotWritingAcceptError,
    by_name,
    named,
    not_writing,
)
from backend.features.runs.repository import RunRepository
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor, make_sender
from tests.migration_helpers import columns_down_and_up
from tests.test_followups import NOW as CHAIN_NOW
from tests.test_followups import _chain_start
from tests.test_letters_advertisers import NOW as OFFER_NOW
from tests.test_letters_advertisers import (
    build_offers,
    donor_of_the_link,
    make_advertiser,
    offers,
    priced_donor,
    queued_offer,
    sent_offer,
)
from tests.test_letters_queue import _build
from tests.test_niche_letters import BOOKIE, _queued_niche_offer
from tests.test_niche_letters import DONOR as NICHE_EXAMPLE
from tests.test_niche_letters import _donor as niche_donor

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)


async def _donor(session: AsyncSession, host: str, *, dr: int | None, review: str | None) -> int:
    domain = await make_donor(session, host, review=review)
    await session.execute(update(DonorModel).where(DonorModel.domain_id == domain.id).values(dr=dr))
    await session.flush()
    return domain.id


async def _run(session: AsyncSession) -> RunModel:
    settings = await RunRepository(session).create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150, units_cap=1
    )
    return await RunRepository(session).create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["crm"],
        country="us",
        status=RunStatus.DONE,
    )


class TestRunScopedBuild:
    async def test_accepted_big_site_does_not_lock_the_build(self, session: AsyncSession) -> None:
        """Поиск адреса крупному не ищет — сборка по прогону не ждёт его вечно."""
        run = await _run(session)
        big = await _donor(session, "big.example.test", dr=88, review="accepted")
        session.add(RunCandidateModel(run_id=run.id, domain_id=big, status="accepted"))
        await session.flush()

        assert await LetterRepository(session).contacts_pending([run.id]) == 0
        await run_scope(LetterRepository(session), [run.id])


class TestLegacyQueueRows:
    async def _legacy(self, session: AsyncSession) -> tuple[RunModel, int]:
        run = await _run(session)
        big = await _donor(session, "big.example.test", dr=88, review=None)
        row = RunCandidateModel(run_id=run.id, domain_id=big)
        session.add(row)
        await session.flush()
        return run, row.id

    async def test_accepting_is_refused_in_words(self, session: AsyncSession) -> None:
        run, row = await self._legacy(session)

        with pytest.raises(NotWritingAcceptError) as refused:
            await RunReview(session).decide(run.id, [row], Decision.ACCEPTED, by="человек")

        assert str(refused.value) == (
            f"Принять нельзя: big.example.test — {big_site_reason(80)}. "
            "Снимите их с выбора — остальных можно принять"
        )
        report = await RunReview(session).decide(run.id, [row], Decision.REJECTED, by="человек")
        assert report.changed == 1, "отклонить можно"

    async def test_waiting_counter_does_not_count_them(self, session: AsyncSession) -> None:
        await self._legacy(session)

        assert (await waiting(session)).domains == 0

    async def test_screen_says_why(
        self, session: AsyncSession, client: AsyncClient, make_user: Any, sign_in: Any
    ) -> None:
        run, row = await self._legacy(session)
        await session.commit()
        await make_user("решает@site.example.test", role=UserRole.OPERATOR)
        token = await sign_in("решает@site.example.test")

        refused = await client.post(
            f"/api/review/runs/{run.id}/decide",
            json={"candidate_ids": [row], "decision": "accepted"},
            headers=bearer(token),
        )

        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"].startswith("Принять нельзя: big.example.test")


#: Домены каждого правила `by_name` и рядом — похожие, которых оно не трогает.
HOSTS = [
    "agency.example.gov",
    "data.gov.uk",
    "uni.ac.uk",
    "x.gouv.fr",
    "my.go.jp",
    "go.jp",
    "go.com",
    "maps.google.co.ke",
    "google-maps-guide.co.ke",
    "example.x.com",
    "t.me",
    sorted(MAJOR_MEDIA)[0],
    "archive.org",
    "blog.example.test",
]


class TestOneCondition:
    async def test_sql_names_the_same_domains_as_the_gate(self, session: AsyncSession) -> None:
        """Одно условие на выходах и до Ahrefs: `named()` на базе = `by_name` в коде."""
        for host in HOSTS:
            await _donor(session, host, dr=30, review="accepted")

        rows = await session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(named())
        )

        assert set(rows.scalars().all()) == set(by_name(HOSTS))

    async def test_zones_and_platforms_get_no_letter_either(self, session: AsyncSession) -> None:
        for host in ("agency.example.gov", "maps.google.co.ke", "blog.example.test"):
            await _donor(session, host, dr=30, review="accepted")

        rows = await session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(~not_writing())
        )

        assert rows.scalars().all() == ["blog.example.test"]


class TestBypassPaths:
    async def test_refind_does_not_pay_for_them(self, session: AsyncSession) -> None:
        await _donor(session, "example.com", dr=88, review="accepted")
        queue = NamedDomains(session, ["https://www.example.com/"], write=True)

        assert await queue.pending_hosts() == []
        assert queue.domains["example.com"].skipped == (f"{big_site_reason(80)} — адрес не ищем")

    async def test_stage_two_does_not_crawl_them(self, session: AsyncSession) -> None:
        for host, dr in (("big.example.test", 88), ("blog.example.test", 45)):
            domain_id = await _donor(session, host, dr=dr, review="accepted")
            await session.execute(
                update(DonorModel)
                .where(DonorModel.domain_id == domain_id)
                .values(last_price=Decimal("90"), last_price_at=datetime.now(UTC))
            )
        await session.flush()

        chosen = await targets.choose(session, limit=10)

        assert chosen.hosts == ["blog.example.test"]
        assert "big.example.test" not in chosen.no_price + chosen.stale_price


class TestAdvertiserOffers:
    async def test_no_offer_on_a_platform_nobody_writes_to(self, session: AsyncSession) -> None:
        """Оффер «мы дешевле» на площадке крупного сайта — та же «кому не пишем»."""
        for donor_host, dr in (("big.example.test", 88), ("blog.example.test", 45)):
            domain = await priced_donor(session, donor_host)
            await session.execute(
                update(DonorModel).where(DonorModel.domain_id == domain.id).values(dr=dr)
            )
            await make_advertiser(session, f"buyer-{dr}.example.test", donor_host=donor_host)
        await session.flush()

        funnel = await Recipients(session).advertiser_funnel()

        assert (funnel.with_link, funnel.fresh_price) == (2, 1)


class TestQueuedOffer:
    """Оффер, собранный до правила на площадке крупного сайта, при отправке не уходит:
    «остановлено» с причиной, как письмо донору оттуда (`offer_standing.py`)."""

    async def test_it_is_stopped_with_its_reason(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await queued_offer(session)
        (await donor_of_the_link(session)).dr = 88
        await session.flush()

        with pytest.raises(NotWritingError) as refused:
            await Sending(session, NullTransport(), now=OFFER_NOW).send(letter.id)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, str(refused.value))
        assert str(refused.value) == (
            f"Письмо №{letter.id} остановлено: площадка оффера donor.example.test — из «кому "
            f"не пишем» ({big_site_reason(80)}). Рекламодателю brand.example.test её не предлагаем"
        )

    async def test_the_batch_names_it_and_the_queue_lets_it_go(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await queued_offer(session)
        (await donor_of_the_link(session)).dr = 88
        await session.flush()

        report = await batch.send_queue(session, NullTransport(), stage=Stage.ADVERTISERS)

        assert (report.sent, report.refused, report.left) == (0, {"кому не пишем": 1}, 0)


class TestOfferFollowup:
    async def test_the_reminder_is_stopped_with_the_offers_reason(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивка повторяет предложение площадки, которое невыполнимо: оффер ушёл до
        правила — добивка «остановлено» с той же причиной, цепочка кончилась."""
        await sent_offer(session, followup_days=[1, 2])
        (await donor_of_the_link(session)).dr = 88
        await session.flush()

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=OFFER_NOW + timedelta(days=2)
        )

        assert (report.stopped, report.sent) == (1, 0)
        reminder = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert reminder is not None
        assert reminder.status is MessageStatus.STOPPED
        assert reminder.failure_reason == (
            f"Письмо №{reminder.id} остановлено: площадка оффера donor.example.test — из «кому "
            f"не пишем» ({big_site_reason(80)}). Рекламодателю brand.example.test её не предлагаем"
        )


class TestNicheExample:
    """Пример площадки бизнесу ниши — правилом сборки (`niche_recipients._examples`), и «кому
    не пишем» в нём нет: ни новое письмо, ни собранное до правила такой пример не назовут."""

    async def test_the_build_does_not_offer_it(self, session: AsyncSession) -> None:
        for host, dr in (("big.example.test", 88), ("blog.example.test", 45)):
            await priced_donor(session, host)
            await session.execute(
                update(DonorModel)
                .where(
                    DonorModel.domain_id.in_(select(DomainModel.id).where(DomainModel.host == host))
                )
                .values(dr=dr, review="accepted")
            )
        run = await _run(session)
        run.candidates = {"hosts": ["big.example.test", "blog.example.test"]}
        await session.flush()

        examples = await NicheRecipients(session).fresh_examples(run, datetime.now(UTC))

        assert examples == ["blog.example.test"]

    async def test_a_letter_built_before_the_rule_is_taken_off_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо, собранное до правила с таким примером, не уходит и в очереди не ждёт:
        ни перезапрос цены, ни решение человека пример в предложение не вернут. До
        12.10.2026 отказ говорил про цену, и письмо стояло в очереди, советуя её
        перезапросить. Письмо не уходило — снято своей причиной, как под снятую ссылку."""
        letter = await _queued_niche_offer(session)
        (await niche_donor(session)).dr = 88
        await session.flush()

        with pytest.raises(NotWritingError) as refused:
            await Sending(session, NullTransport()).send(letter.id)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (
            MessageStatus.STOPPED,
            attempts.EXAMPLE_WITHDRAWN,
        )
        assert str(refused.value) == (
            f"Письмо №{letter.id} снято: пример площадки {NICHE_EXAMPLE} — из «кому не пишем» "
            f"({big_site_reason(80)}). Бизнесу ниши {BOOKIE} её не предлагаем — следующая "
            "сборка напишет с примером оттуда, где пишем"
        )

    async def test_the_next_build_writes_with_another_example(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Бизнес не пропадает молча (ревью продаж): снятое письмо — не «писали», и
        следующая сборка пишет ему заново с примером оттуда, где пишем."""
        letter = await _queued_niche_offer(session)
        (await niche_donor(session)).dr = 88
        await priced_donor(session, "small.example.test")
        (await niche_donor(session, "small.example.test")).dr = 40
        run = await session.scalar(select(RunModel))
        assert run is not None
        run.candidates = {"hosts": [*run_hosts(run), "small.example.test"]}
        await session.flush()
        with pytest.raises(NotWritingError):
            await Sending(session, NullTransport()).send(letter.id)

        await build_offers(session, audience=NICHE, campaign_name="Ниша снова")

        again = [message for message in await offers(session) if message.id != letter.id]
        assert [message.status for message in again] == [MessageStatus.QUEUED]
        assert "small.example.test" in (again[0].body or "")
        assert NICHE_EXAMPLE not in (again[0].body or "")

    async def test_a_second_example_from_the_list_stops_it_too(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Запрет не зависит от того, что ещё названо: человек дописал второй пример —
        из «кому не пишем», — и свежий первый письмо не пропускает (ревью продаж)."""
        letter = await _queued_niche_offer(session)
        await priced_donor(session, "big.example.test")
        (await niche_donor(session, "big.example.test")).dr = 88
        run = await session.scalar(select(RunModel))
        assert run is not None
        run.candidates = {"hosts": [*run_hosts(run), "big.example.test"]}
        letter.body = f"{letter.body}\nИ ещё, например, big.example.test."
        await session.flush()

        with pytest.raises(NotWritingError, match=re.escape("пример площадки big.example.test")):
            await Sending(session, NullTransport()).send(letter.id)

    async def test_the_batch_names_it_and_the_queue_lets_it_go(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queued_niche_offer(session)
        (await niche_donor(session)).dr = 88
        await session.flush()

        report = await batch.send_queue(
            session, NullTransport(), stage=Stage.ADVERTISERS, audience=NICHE
        )

        assert (report.sent, report.refused, report.left) == (0, {"кому не пишем": 1}, 0)


class TestTheQueueCounter:
    async def test_letters_nobody_writes_to_are_not_counted(self, session: AsyncSession) -> None:
        for host in ("big.example.test", "blog.example.test"):
            await make_donor(session, host, email=f"info@{host}")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        await session.execute(
            update(DonorModel)
            .where(
                DonorModel.domain_id.in_(
                    select(DomainModel.id).where(DomainModel.host == "big.example.test")
                )
            )
            .values(dr=88)
        )
        await session.flush()

        assert await LetterRepository(session).queued_count(stage=Stage.DONORS) == 1


class TestFollowupThroughThePass:
    async def test_followup_is_stopped_with_its_reason(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Сквозной путь добивки: проход `send_due` — «остановлено», цепочка кончилась."""
        message, _ = await _chain_start(session, followup_days=[1, 2])
        await session.execute(
            update(DonorModel).where(DonorModel.domain_id == message.domain_id).values(dr=88)
        )
        await session.flush()

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=CHAIN_NOW + timedelta(days=2)
        )

        assert (report.stopped, report.sent) == (1, 0)
        followup = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert followup is not None
        assert followup.status is MessageStatus.STOPPED
        assert big_site_reason(80) in (followup.failure_reason or "")


class TestTheVersionHoldsTheLimit:
    async def test_measurement_judges_by_the_version(self) -> None:
        assert check_dr(85, T) is not None
        assert check_dr(85, replace(T, max_dr=90)) is None

    async def test_exits_take_the_version_in_force(self, session: AsyncSession) -> None:
        await _donor(session, "big.example.test", dr=85, review="accepted")
        await RunRepository(session).create_settings(
            replace(T, max_dr=90),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=1,
        )

        found = await session.scalar(select(DonorModel.id).where(not_writing()))

        assert found is None, "DR 85 при пороге 90 — не крупный"

    async def test_screen_saves_the_limit_and_the_next_version_carries_it(
        self, client: AsyncClient, session: AsyncSession, make_user: Any, sign_in: Any
    ) -> None:
        await make_user("пороги@site.example.test", role=UserRole.ADMIN)
        token = await sign_in("пороги@site.example.test")
        body = {"min_dr": 20, "min_org_traffic": 500, "min_refdomains": 100, "min_keywords": 300}

        first = await client.post(
            "/api/settings/thresholds", json={**body, "max_dr": 75}, headers=bearer(token)
        )
        again = await client.post("/api/settings/thresholds", json=body, headers=bearer(token))

        assert (first.status_code, again.status_code) == (200, 200), again.text
        assert (first.json()["max_dr"], again.json()["max_dr"]) == (75, 75)

    async def test_migration_goes_down_and_up(self, session: AsyncSession) -> None:
        connection = await session.connection()

        found = await connection.run_sync(
            columns_down_and_up, "b5e0c3f94a17_run_settings_max_dr.py", "run_settings", {"max_dr"}
        )

        assert found == (set(), {"max_dr"})
