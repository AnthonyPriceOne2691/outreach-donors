"""Экран ручной проверки: кто смотрит, кто решает и что в журнале.

Экран существует ради одного числа: требование ограничивает долю ложных
рекламодателей десятью процентами, а скоринг судит по пяти признакам,
два из которых выведены из замера на одной нише. Поэтому проверяется
не только «работает ли ручка», но и то, ради чего она заведена: балл
скоринга решением человека не переписывается, а повторное решение
не проходит молча.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import (
    AuditAction,
    CrawlOutcome,
    StopReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

DONOR = "donor.example.test"

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/advertisers", None, "view"),
    ("POST", "/api/advertisers/{row}/decide", {"confirmed": True}, "prices"),
]


@pytest.fixture
async def candidate(session: AsyncSession) -> CandidateModel:
    """Пограничный кандидат — тот, ради которого экран и сделан."""
    run = CrawlRunModel(
        host=DONOR,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=12,
        articles=10,
    )
    session.add(run)
    await session.flush()

    row = CandidateModel(
        crawl_run_id=run.id,
        donor_host=DONOR,
        target_root="advertiser.example",
        points=3,
        verdict=Verdict.PENDING,
        reasons=["коммерческий анкор +1", "коммерческий анкор под nofollow +2"],
        links=2,
        pages=2,
        best_page_url=f"https://{DONOR}/post/1",
        best_anchor="Bet now",
    )
    session.add(row)
    await session.commit()
    return row


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


def _path(path: str, row: CandidateModel) -> str:
    return path.replace("{row}", str(row.id))


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        candidate: CandidateModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, _path(path, candidate), json=body)

        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_operator_does_the_work(
        self,
        client: AsyncClient,
        operator_token: str,
        candidate: CandidateModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Ручная проверка — работа оператора, а не админа: иначе она
        упирается в того, кого зовут раз в неделю."""
        response = await client.request(
            method, _path(path, candidate), json=body, headers=bearer(operator_token)
        )

        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/advertisers")
            for method in methods
        }
        in_table = {
            (method, path.replace("{row}", "{candidate_id}")) for method, path, _, _ in ROUTES
        }

        assert in_app == in_table


class TestSeeing:
    async def test_queue_shows_the_borderline_and_its_reasons(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        response = await client.get("/api/advertisers", headers=bearer(admin_token))

        body = response.json()
        assert body["waiting"] == 1
        row = body["rows"][0]
        assert row["target_root"] == "advertiser.example"
        assert row["points"] == 3
        assert "коммерческий анкор под nofollow +2" in row["reasons"]

    async def test_letter_gets_the_page_and_the_anchor(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        """Требование просит писать «под конкретную найденную ссылку —
        страницу и анкор». Обе на карточке."""
        response = await client.get("/api/advertisers", headers=bearer(admin_token))

        row = response.json()["rows"][0]
        assert row["best_page_url"].endswith("/post/1")
        assert row["best_anchor"] == "Bet now"

    async def test_counts_show_the_blocked_too(
        self,
        client: AsyncClient,
        admin_token: str,
        candidate: CandidateModel,
        session: AsyncSession,
    ) -> None:
        """По отсеянным видно, что список «кому не пишем» работает,
        а не молчит."""
        session.add(
            CandidateModel(
                crawl_run_id=candidate.crawl_run_id,
                donor_host=DONOR,
                target_root="facebook.com",
                points=0,
                verdict=Verdict.BLOCKED,
                reasons=["кому не пишем: social (facebook.com)"],
                links=3,
                pages=3,
            )
        )
        await session.commit()

        response = await client.get("/api/advertisers", headers=bearer(admin_token))

        assert response.json()["counts"]["blocked"] == 1
        assert len(response.json()["rows"]) == 1

    async def test_decided_are_out_of_the_queue_but_can_be_asked_for(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        queue = await client.get("/api/advertisers", headers=bearer(admin_token))
        assert queue.json()["rows"] == []
        assert queue.json()["waiting"] == 0

        with_decided = await client.get(
            "/api/advertisers?include_decided=true", headers=bearer(admin_token)
        )
        assert len(with_decided.json()["rows"]) == 1


class TestDeciding:
    async def test_decision_does_not_overwrite_the_score(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        """По расхождению между баллом и решением и считается, как часто
        скоринг ошибается. Перезаписав балл, мы получили бы базу,
        по которой он всегда прав."""
        response = await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        body = response.json()
        assert body["confirmed"] is True
        assert body["points"] == 3
        assert body["verdict"] == "pending"
        assert body["decided_by"] == "админ@site.com"

    async def test_second_decision_is_refused_not_silently_applied(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        """Два человека, открывшие одну очередь, иначе затрут решения
        друг друга и не узнают об этом."""
        await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        again = await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": False},
            headers=bearer(admin_token),
        )

        assert again.status_code == 409

    async def test_changing_your_mind_works_when_said_out_loud(
        self, client: AsyncClient, admin_token: str, candidate: CandidateModel
    ) -> None:
        await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        again = await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": False, "force": True},
            headers=bearer(admin_token),
        )

        assert again.status_code == 200
        assert again.json()["confirmed"] is False

    async def test_unknown_candidate_is_a_plain_404(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        response = await client.post(
            "/api/advertisers/999999/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        assert response.status_code == 404

    async def test_decision_lands_in_the_journal(
        self,
        client: AsyncClient,
        admin_token: str,
        candidate: CandidateModel,
        session: AsyncSession,
    ) -> None:
        """«Откуда у нас этот адресат» спросит либо сам адресат,
        либо юрист."""
        await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": True},
            headers=bearer(admin_token),
        )

        entry = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.ADVERTISER_REVIEWED)
            )
        ).scalar_one()
        assert entry.details["рекламодатель"] == "advertiser.example"
        assert entry.details["решение"] == "пишем"
        assert entry.details["балл скоринга"] == 3
