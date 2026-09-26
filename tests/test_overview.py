"""Главная: числа сводки считаются правилом своего экрана, на настоящей базе.

Сводка не заводит своих правил — она зовёт функции экранов. Поэтому
проверяется не арифметика, а то, что по каждому числу видно, какое правило
его считает: домен в двух очередях — один раз, донор с тремя письмами —
один «написали», отказ доставки — тоже ушедшее письмо, ответ, ждущий
разбора, — работа, а не «ответил».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core import usage
from backend.features.core.domain import (
    ContactStatus,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    RunStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.ops.overview import overview
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/overview", None, "view"),
]


async def _run(session: AsyncSession) -> RunModel:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    return await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["garden blog write for us", "garden tools review"],
        country="us",
        status=RunStatus.DONE,
    )


async def _donor(session: AsyncSession, host: str, **fields: Any) -> DomainModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, **fields))
    await session.flush()
    return domain


async def _campaign(session: AsyncSession, stage: Stage = Stage.DONORS) -> CampaignModel:
    campaign = CampaignModel(stage=stage, name=f"Проверка {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    return campaign


async def _letter(
    session: AsyncSession,
    campaign: CampaignModel,
    domain: DomainModel,
    status: MessageStatus,
    *,
    step: int = 0,
    thread: ThreadModel | None = None,
) -> MessageModel:
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id if thread else None,
        domain_id=domain.id,
        step=step,
        status=status,
        sent_at=None if status is MessageStatus.QUEUED else NOW - timedelta(days=2),
        idempotency_key=f"test:{domain.host}:{campaign.stage.value}:{step}",
    )
    session.add(message)
    await session.flush()
    return message


async def _thread(
    session: AsyncSession, campaign: CampaignModel, domain: DomainModel
) -> ThreadModel:
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    return thread


class TestDonors:
    async def test_each_count_is_its_own_rule(self, session: AsyncSession) -> None:
        await _donor(
            session,
            "priced.example.test",
            status=DonorStatus.SUITABLE,
            review="accepted",
            contact_status=ContactStatus.FOUND,
            last_price=Decimal(250),
            last_price_currency="EUR",
            last_price_at=NOW - timedelta(days=10),
        )
        await _donor(
            session,
            "stale-price.example.test",
            status=DonorStatus.SUITABLE,
            review="accepted",
            contact_status=ContactStatus.FOUND,
            last_price=Decimal(90),
            last_price_currency="USD",
            last_price_at=NOW - timedelta(days=400),
        )
        await _donor(
            session,
            "form.example.test",
            status=DonorStatus.SUITABLE,
            review="rejected",
            contact_status=ContactStatus.FORM_ONLY,
        )
        await _donor(session, "unchecked.example.test", status=DonorStatus.UNCHECKED)
        await _donor(session, "weak.example.test", status=DonorStatus.UNSUITABLE)

        donors = (await overview(session)).donors

        assert (donors.total, donors.unchecked, donors.suitable) == (5, 1, 3)
        assert (donors.accepted, donors.rejected) == (2, 1)
        # Воронка ниже «доноров» — среди доноров (решение 26.09.2026): форма
        # у отклонённого домена донора не делает, и в «с формой» он не входит.
        assert (donors.with_email, donors.form_only) == (2, 0)
        # Цена старше срока годности — цена, но не свежая: по ней уже не работают.
        assert (donors.priced, donors.priced_fresh) == (2, 1)

    async def test_written_counts_donors_not_letters(self, session: AsyncSession) -> None:
        """Донор с первым письмом и добивкой — один «написали»; отказ доставки
        — тоже ушедшее письмо; письмо в очереди — ещё нет."""
        campaign = await _campaign(session)
        twice = await make_donor(session, "twice.example.test")
        bounced = await make_donor(session, "bounced.example.test")
        waiting = await make_donor(session, "waiting.example.test")
        await _letter(session, campaign, twice, MessageStatus.DELIVERED)
        await _letter(session, campaign, twice, MessageStatus.SENT, step=1)
        await _letter(session, campaign, bounced, MessageStatus.BOUNCED)
        await _letter(session, campaign, waiting, MessageStatus.QUEUED)
        # Письмо рекламодателю в «написали донорам» не входит.
        stage_two = await _campaign(session, Stage.ADVERTISERS)
        await _letter(session, stage_two, waiting, MessageStatus.SENT)

        assert (await overview(session)).donors.written == 2


class TestWaiting:
    async def test_domain_waits_once_even_in_two_queues(self, session: AsyncSession) -> None:
        older, newer = await _run(session), await _run(session)
        both = await make_donor(session, "both.example.test", review=None)
        only_new = await make_donor(session, "only-new.example.test", review=None)
        decided = await make_donor(session, "decided.example.test")
        session.add_all(
            [
                RunCandidateModel(run_id=older.id, domain_id=both.id, status="pending"),
                RunCandidateModel(run_id=newer.id, domain_id=both.id, status="pending"),
                RunCandidateModel(run_id=newer.id, domain_id=only_new.id, status="pending"),
                RunCandidateModel(run_id=older.id, domain_id=decided.id, status="accepted"),
            ]
        )
        await session.flush()

        view = await overview(session)

        assert view.waiting.review == 2
        # Новые первыми: плитка ведёт в самую свежую очередь.
        assert view.waiting.review_runs == [newer.id, older.id]

    async def test_resolved_queue_is_not_listed(self, session: AsyncSession) -> None:
        run = await _run(session)
        domain = await make_donor(session, "done.example.test")
        session.add(RunCandidateModel(run_id=run.id, domain_id=domain.id, status="rejected"))
        await session.flush()

        waiting = (await overview(session)).waiting

        assert (waiting.review, waiting.review_runs) == (0, [])

    async def test_unsure_price_is_work_and_answers_count_by_donor(
        self, session: AsyncSession
    ) -> None:
        """Ответ, где цену подтверждает человек, — работа, а не «ответил»;
        автоответчик не ответ вовсе; два адреса одного донора — один ответивший."""
        campaign = await _campaign(session)
        unsure = await make_donor(session, "unsure.example.test")
        priced = await make_donor(session, "priced.example.test")
        away = await make_donor(session, "away.example.test")

        unsure_thread = await _thread(session, campaign, unsure)
        await _letter(session, campaign, unsure, MessageStatus.DELIVERED, thread=unsure_thread)
        session.add(
            ReplyModel(
                thread_id=unsure_thread.id,
                kind=ReplyKind.HUMAN,
                raw_body="Maybe 300, depends on the topic.",
                price_white=300,
                currency="USD",
                confidence=0.4,
            )
        )
        for number in range(2):
            thread = await _thread(session, campaign, priced)
            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    thread_id=thread.id,
                    domain_id=priced.id,
                    step=0,
                    status=MessageStatus.DELIVERED,
                    sent_at=NOW - timedelta(days=2),
                    idempotency_key=f"test:priced:{number}",
                )
            )
            session.add(
                ReplyModel(
                    thread_id=thread.id,
                    kind=ReplyKind.HUMAN,
                    raw_body="Placement is 250 EUR.",
                    price_white=250,
                    currency="EUR",
                    confidence=0.95,
                )
            )
        away_thread = await _thread(session, campaign, away)
        await _letter(session, campaign, away, MessageStatus.DELIVERED, thread=away_thread)
        session.add(
            ReplyModel(thread_id=away_thread.id, kind=ReplyKind.AUTO_REPLY, raw_body="On leave.")
        )
        await session.flush()

        view = await overview(session)

        assert view.waiting.prices == 1
        assert view.donors.replied == 2


class TestLettersAndSpending:
    async def test_letters_are_counted_by_stage(self, session: AsyncSession) -> None:
        donors_campaign = await _campaign(session)
        advertisers_campaign = await _campaign(session, Stage.ADVERTISERS)
        for number, status in enumerate(
            [
                MessageStatus.QUEUED,
                MessageStatus.QUEUED,
                MessageStatus.SENT,
                MessageStatus.DELIVERED,
                MessageStatus.BOUNCED,
            ]
        ):
            domain = await make_donor(session, f"d{number}.example.test")
            await _letter(session, donors_campaign, domain, status)
        advertiser = await make_donor(session, "brand.example.test")
        await _letter(session, advertisers_campaign, advertiser, MessageStatus.QUEUED)

        letters = (await overview(session)).letters

        donors = letters[Stage.DONORS]
        assert (donors.queued, donors.sent, donors.delivered, donors.bounced) == (2, 3, 1, 1)
        assert letters[Stage.ADVERTISERS].queued == 1

    async def test_units_are_ours_and_cap_is_the_monthly_one(self, session: AsyncSession) -> None:
        usage.record(session, operation="batch_metrics", units=1_200)
        usage.record(session, operation="by_country", units=55)
        usage.record(session, operation="serp_search", amount_usd=Decimal("0.35"))
        await session.flush()

        view = await overview(session)

        assert view.ahrefs_units == 1_255
        assert view.serp_usd == Decimal("0.35")

    async def test_last_run_is_the_history_row(self, session: AsyncSession) -> None:
        await _run(session)
        newest = await _run(session)
        domain = await make_donor(session, "queued.example.test", review=None)
        session.add(RunCandidateModel(run_id=newest.id, domain_id=domain.id, status="pending"))
        await session.flush()

        last = (await overview(session)).last_run

        assert last is not None
        assert last.run.id == newest.id
        # Та же строка, что в истории прогонов: очередь посчитана её правилом.
        assert last.queue == {"pending": 1}

    async def test_empty_base_is_zeros_not_a_failure(self, session: AsyncSession) -> None:
        view = await overview(session)

        assert view.donors.total == 0
        assert view.last_run is None
        assert view.waiting.review_runs == []


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self, client: AsyncClient, method: str, path: str, body: Any, permission: str
    ) -> None:
        response = await client.request(method, path, json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_operator_sees_the_overview(
        self,
        client: AsyncClient,
        operator_token: str,
        method: str,
        path: str,
        body: Any,
        permission: str,
    ) -> None:
        response = await client.request(method, path, json=body, headers=bearer(operator_token))
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_the_permission_refused_by_name(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        method: str,
        path: str,
        body: Any,
        permission: str,
    ) -> None:
        await make_user("без-права@site.com", permissions={permission: False})
        token = await sign_in("без-права@site.com")

        response = await client.request(method, path, json=body, headers=bearer(token))

        assert response.status_code == 403
        assert f"«{permission}»" in response.json()["detail"]

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/overview")
            for method in methods
        }
        assert in_app == {(method, path) for method, path, _, _ in ROUTES}


class TestShape:
    async def test_mail_state_and_stages_are_in_the_answer(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        """Почта и оба этапа приходят всегда — пустой этап это нули, а не
        отсутствие ключа, на котором экран упал бы."""
        response = await client.get("/api/overview", headers=bearer(operator_token))

        body = response.json()
        assert set(body["letters"]) == {"donors", "advertisers"}
        assert body["transport"]["real"] is False
        assert body["last_run"] is None
