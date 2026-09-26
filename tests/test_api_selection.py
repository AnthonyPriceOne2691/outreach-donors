"""Экран отбора: кто где лежит, кто решает и что остаётся от машины.

Экран существует ради двух чисел — доли ложно принятых и ложно
отсеянных, — и оба считаются по расхождению человека с машиной. Поэтому
проверяется не только «ручка работает», но и то, ради чего она заведена:
вердикт судьи решением человека не переписывается, отрезанный до Ahrefs
домен виден, а решение человека сильнее модели в следующем прогоне.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import AuditAction, DonorStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.repository import RunRepository
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/selection", None, "view"),
    ("POST", "/api/selection/{row}/decide", {"intent": "publisher"}, "prices"),
]

NOW = datetime.now(UTC)


async def _domain(
    session: AsyncSession,
    host: str,
    *,
    status: DonorStatus | None,
    reason: str | None = None,
    recommendation: str | None = None,
    decided_by: str | None = None,
    quote: str | None = None,
) -> DomainModel:
    domain = DomainModel(host=host)
    if recommendation is not None:
        domain.judge_recommendation = recommendation
        domain.site_intent = {"accept": "refers_out", "reject": "sells_own"}.get(
            recommendation, "unknown"
        )
        domain.judge_decided_by = decided_by or "model"
        # Вердикт, вынесенный моделью, несёт её имя; строка без него — след
        # сбоя модели, и кэш судьи её вердиктом не считает.
        domain.judge_model = "gpt-5-mini"
        domain.judge_quote = quote
        domain.judge_reason = "по тексту выдачи"
        domain.judged_at = NOW
    session.add(domain)
    await session.flush()
    if status is not None:
        session.add(DonorModel(domain_id=domain.id, status=status, reject_reason=reason, dr=40))
    return domain


@pytest.fixture
async def field(session: AsyncSession) -> dict[str, DomainModel]:
    """По домену на каждый исход отбора."""
    made = {
        "media": await _domain(session, "media.test", status=DonorStatus.SUITABLE,
                               recommendation="accept"),
        "brand": await _domain(session, "brand.test", status=DonorStatus.SUITABLE,
                               recommendation="reject", decided_by="rule", quote="Buy direct"),
        "weak": await _domain(session, "weak.test", status=DonorStatus.UNSUITABLE,
                              reason="органический трафик 100 ниже 500", recommendation="accept"),
        # Отрезан судьёй во включённом режиме: до Ahrefs не дошёл, донора нет.
        "cut": await _domain(session, "cut.test", status=None, recommendation="reject",
                             quote="Sign up and bet"),
        "unsure": await _domain(session, "unsure.test", status=DonorStatus.SUITABLE,
                                recommendation="review"),
        # База, собранная до судьи.
        "old": await _domain(session, "old.test", status=DonorStatus.SUITABLE),
    }  # fmt: skip
    await session.commit()
    return made


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _hosts(client: AsyncClient, token: str, **params: Any) -> list[str]:
    response = await client.get("/api/selection", params=params, headers=bearer(token))
    assert response.status_code == 200, response.text
    return [row["host"] for row in response.json()["rows"]]


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        field: dict[str, DomainModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        url = path.replace("{row}", str(field["brand"].id))
        assert (await client.request(method, url, json=body)).status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_operator_does_the_work(
        self,
        client: AsyncClient,
        operator_token: str,
        field: dict[str, DomainModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Разбор — работа оператора: иначе он упирается в того, кого зовут
        раз в неделю."""
        url = path.replace("{row}", str(field["brand"].id))
        response = await client.request(method, url, json=body, headers=bearer(operator_token))
        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/selection")
            for method in methods
        }
        in_table = {(method, path.replace("{row}", "{domain_id}")) for method, path, _, _ in ROUTES}
        assert in_app == in_table


class TestTabs:
    async def test_each_domain_lies_in_exactly_one_tab(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        accepted = await _hosts(client, admin_token, tab="accepted")
        review = await _hosts(client, admin_token, tab="review")
        rejected = await _hosts(client, admin_token, tab="rejected")

        assert sorted(accepted) == ["media.test", "old.test"]
        assert review == ["unsure.test"]
        assert sorted(rejected) == ["brand.test", "cut.test", "weak.test"]

    async def test_domain_cut_before_ahrefs_is_still_visible(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        """Во включённом судье отрезанный донором не становится. Список
        доноров его бы не показал — а отклонённый без следа и есть та слепая
        зона, ради которой экран заведён."""
        response = await client.get(
            "/api/selection", params={"tab": "rejected"}, headers=bearer(admin_token)
        )
        row = next(r for r in response.json()["rows"] if r["host"] == "cut.test")
        assert row["donor_id"] is None
        assert row["machine"]["quote"] == "Sign up and bet"

    async def test_threshold_reason_names_the_numbers(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        response = await client.get(
            "/api/selection", params={"tab": "rejected"}, headers=bearer(admin_token)
        )
        row = next(r for r in response.json()["rows"] if r["host"] == "weak.test")
        assert row["reject_reason"] == "органический трафик 100 ниже 500"

    async def test_counts_cover_all_tabs_not_the_page(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        response = await client.get(
            "/api/selection", params={"limit": 1}, headers=bearer(admin_token)
        )
        assert response.json()["tabs"] == {"accepted": 2, "review": 1, "rejected": 3}

    async def test_unjudged_filter_shows_the_old_base(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        """«Принят» у базы до судьи значит только «прошёл пороги»."""
        assert await _hosts(client, admin_token, tab="accepted", judge="none") == ["old.test"]


class TestHumanDecision:
    async def test_human_is_stronger_but_machine_stays(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        field: dict[str, DomainModel],
    ) -> None:
        brand = field["brand"]
        response = await client.post(
            f"/api/selection/{brand.id}/decide",
            json={"intent": "publisher", "note": "статьи принимают"},
            headers=bearer(admin_token),
        )

        body = response.json()
        assert body["tab"] == "accepted", "решение человека сильнее модели"
        assert body["disagrees"] is True
        # ⚠ Вердикт машины не тронут: по расхождению и считается точность.
        assert body["machine"]["recommendation"] == "reject"
        assert body["machine"]["decided_by"] == "rule"
        assert body["human"]["note"] == "статьи принимают"

    async def test_disagreement_is_scored_per_layer(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        """Точность считается по слою: правилу верят без взгляда человека,
        и его ошибка — другая новость, чем ошибка модели."""
        await client.post(
            f"/api/selection/{field['brand'].id}/decide",
            json={"intent": "publisher"},
            headers=bearer(admin_token),
        )
        await client.post(
            f"/api/selection/{field['media'].id}/decide",
            json={"intent": "publisher"},
            headers=bearer(admin_token),
        )
        await client.post(
            f"/api/selection/{field['unsure'].id}/decide",
            json={"intent": "sells_own"},
            headers=bearer(admin_token),
        )

        body = (await client.get("/api/selection", headers=bearer(admin_token))).json()
        assert body["reviewed"] == 3
        assert body["disagreements"] == 1
        assert body["layers"] == {
            "rule": {"checked": 1, "agreed": 0},
            "model": {"checked": 1, "agreed": 1},
        }, "«посмотри» машины — просьба, а не мнение: в точность не идёт"

    async def test_decision_can_be_taken_back(
        self, client: AsyncClient, admin_token: str, field: dict[str, DomainModel]
    ) -> None:
        url = f"/api/selection/{field['brand'].id}/decide"
        await client.post(url, json={"intent": "publisher"}, headers=bearer(admin_token))
        response = await client.post(url, json={"intent": None}, headers=bearer(admin_token))

        body = response.json()
        assert body["tab"] == "rejected"
        assert body["human"]["intent"] is None

    async def test_decision_lands_in_the_journal(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        field: dict[str, DomainModel],
    ) -> None:
        await client.post(
            f"/api/selection/{field['brand'].id}/decide",
            json={"intent": "non_commercial"},
            headers=bearer(admin_token),
        )

        record = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.SITE_REVIEWED)
            )
        ).scalar_one()
        assert record.details["домен"] == "brand.test"
        assert record.details["решение"] == "non_commercial"
        assert record.details["судья"] == "reject"

    async def test_unknown_domain_is_404(self, client: AsyncClient, admin_token: str) -> None:
        response = await client.post(
            "/api/selection/999999/decide",
            json={"intent": "publisher"},
            headers=bearer(admin_token),
        )
        assert response.status_code == 404


class TestSellerAnswer:
    async def test_donor_answer_is_stronger_than_judge_and_human(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        field: dict[str, DomainModel],
    ) -> None:
        """Для гест-постинга ответ самого сайта — правда первого сорта."""
        media = field["media"]
        media.human_intent = "publisher"
        media.seller_answer = "declines"
        media.seller_answer_at = NOW
        await session.commit()

        rejected = await _hosts(client, admin_token, tab="rejected")
        assert "media.test" in rejected

    async def test_judge_is_scored_against_donor_answers(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        field: dict[str, DomainModel],
    ) -> None:
        """Главное число для гест-постинга: как часто судья угадал, продаёт
        ли сайт размещение, — по ответам самих сайтов, по каждому слою."""
        field["media"].seller_answer = "sells"  # судья: площадка — угадал
        field["weak"].seller_answer = "declines"  # судья: площадка — промах
        field["unsure"].seller_answer = "sells"  # судья: «посмотри» — не в счёт
        await session.commit()

        body = (await client.get("/api/selection", headers=bearer(admin_token))).json()
        assert body["answered"] == 3
        assert body["answer_layers"] == {"model": {"checked": 2, "agreed": 1}}

        answered = await _hosts(client, admin_token, tab="rejected", answer="answered")
        assert answered == ["weak.test"]


class TestNextRun:
    async def test_human_verdict_wins_and_never_expires(
        self, session: AsyncSession, field: dict[str, DomainModel]
    ) -> None:
        """Домен с решением человека не пересуживается: мнение модели
        всё равно ничего бы не решило, а токены стоило бы."""
        brand = field["brand"]
        brand.human_intent = "publisher"
        brand.judged_at = NOW - timedelta(days=1000)
        await session.commit()

        found = await DonorRepository(session).fresh_judged(["brand.test", "media.test"])

        assert found["brand.test"] == "accept"
        assert found["media.test"] == "accept"


class TestRunReport:
    async def test_run_shows_how_often_the_human_disagreed(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        field: dict[str, DomainModel],
    ) -> None:
        """Доля расхождений в отчёте прогона считается при чтении: человек
        решает после прогона, и записанная в его конце доля была бы нулём."""
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
            keywords=["a"],
            country="us",
            candidates={"hosts": ["brand.test", "media.test", "unsure.test"]},
        )
        session.add(run)
        await session.commit()

        for host, intent in (("brand", "publisher"), ("media", "publisher")):
            await client.post(
                f"/api/selection/{field[host].id}/decide",
                json={"intent": intent},
                headers=bearer(admin_token),
            )

        card = (await client.get(f"/api/runs/{run.id}", headers=bearer(admin_token))).json()
        assert card["reviewed"] == 2
        assert card["disagreements"] == 1, "правило отрезало бы brand.test, человек не согласен"
