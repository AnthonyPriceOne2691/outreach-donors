"""Бизнесы ниши из выдачи прогона — в кандидаты в рекламодатели.

Решение Anthony 04.10.2026: сайт, который сам продаёт в нише прогона (судья:
«продаёт своё»), — не донор, а кандидат в рекламодатели. Проверяется на
настоящей базе то, чего не видно по зелёному прогону: слово человека главнее
судьи в обе стороны; принятый донор и уже известный рекламодатель не
попадают; повторный сбор не дублирует; до «пишем» человека поиск адреса
бизнес не трогает; сбор в конце прогона пишет итог в его статистику.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import AuditAction, RunStatus, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_execute_run import GOOD, FakeSerp, T, _ahrefs, _deps, _settings_id

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _run(session: AsyncSession, hosts: list[str]) -> int:
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
        keywords=["sports betting"],
        country="de",
        candidates={"hosts": hosts, "found_by": {host: ["sports betting"] for host in hosts}},
    )
    session.add(run)
    await session.flush()
    return run.id


async def _domain(
    session: AsyncSession, host: str, *, judge: str | None = None, human: str | None = None
) -> DomainModel:
    domain = DomainModel(host=host, site_intent=judge, human_intent=human)
    session.add(domain)
    await session.flush()
    return domain


async def _advertisers(session: AsyncSession) -> dict[str, AdvertiserModel]:
    rows = await session.execute(
        select(DomainModel.host, AdvertiserModel).join(
            AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id
        )
    )
    return dict(rows.tuples().all())


class TestWhoIsANicheBusiness:
    async def test_human_word_beats_the_judge_both_ways(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        await _domain(session, "blog.example.test", judge="sells_own", human="publisher")
        await _domain(session, "shop.example.test", judge="editorial_ads", human="sells_own")
        await _domain(session, "news.example.test", judge="editorial_ads")
        run_id = await _run(
            session,
            ["bookie.example.test", "blog.example.test", "shop.example.test", "news.example.test"],
        )

        report = await niche.collect(session, run_id)

        found = await _advertisers(session)
        assert set(found) == {"bookie.example.test", "shop.example.test"}
        assert report == niche.NicheReport(run_id=run_id, found=2, added=2)
        bookie = found["bookie.example.test"]
        assert (bookie.source, bookie.found_run_id) == (niche.NICHE, run_id)
        assert bookie.confirmed_by_human is False
        assert bookie.decided_at is None

    async def test_our_donor_and_known_advertiser_stay_as_they_are(
        self, session: AsyncSession
    ) -> None:
        donor = await _domain(session, "ours.example.test", judge="sells_own")
        session.add(DonorModel(domain_id=donor.id, review="accepted"))
        known = await _domain(session, "known.example.test", judge="sells_own")
        session.add(AdvertiserModel(domain_id=known.id, points=4, donors=1, links=2))
        await session.flush()
        run_id = await _run(session, ["ours.example.test", "known.example.test"])

        report = await niche.collect(session, run_id)
        again = await niche.collect(session, run_id)

        found = await _advertisers(session)
        assert set(found) == {"known.example.test"}
        assert found["known.example.test"].source == niche.LINKS  # не перетёрт
        assert (report.found, report.added) == (1, 0)
        assert again.added == 0

    async def test_unknown_run_says_so(self, session: AsyncSession) -> None:
        with pytest.raises(niche.UnknownNicheRunError, match="№999999"):
            await niche.collect(session, 999_999)


class TestHumanDecision:
    async def test_contact_search_waits_for_write(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        run_id = await _run(session, ["bookie.example.test"])
        await niche.collect(session, run_id)
        bookie = (await _advertisers(session))["bookie.example.test"]
        queue = AdvertiserContactRepository(session)

        assert await queue.pending_hosts(limit=10) == []  # ещё не решили — адрес не ищем
        await niche.decide(session, bookie.id, write=True, by="anthony@site.test")
        assert await queue.pending_hosts(limit=10) == ["bookie.example.test"]

    async def test_skip_is_remembered(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        run_id = await _run(session, ["bookie.example.test"])
        await niche.collect(session, run_id)
        bookie = (await _advertisers(session))["bookie.example.test"]

        decided = (
            await niche.decide(session, bookie.id, write=False, by="anthony@site.test")
        ).advertiser

        assert decided.confirmed_by_human is False
        assert decided.decided_by == "anthony@site.test"
        assert decided.decided_at is not None
        assert await AdvertiserContactRepository(session).pending_hosts(limit=10) == []

    async def test_only_niche_businesses_are_decided_here(self, session: AsyncSession) -> None:
        linked = await _domain(session, "linked.example.test")
        advertiser = AdvertiserModel(domain_id=linked.id, points=4, donors=1, links=2)
        session.add(advertiser)
        await session.flush()

        with pytest.raises(niche.UnknownNicheAdvertiserError):
            await niche.decide(session, advertiser.id, write=True, by="anthony@site.test")


async def test_run_ends_by_collecting_niche_businesses(session: AsyncSession) -> None:
    """Сбор зовётся в конце прогона, и его итог виден в записи прогона."""
    asked: list[int] = []

    async def collector(run_id: int) -> niche.NicheReport:
        asked.append(run_id)
        return niche.NicheReport(run_id=run_id, found=3, added=2)

    serp = FakeSerp(["https://good.com/a"])
    deps = replace(await _deps(session, serp, _ahrefs({"good.com": GOOD})), niche=collector)

    report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

    run = (await session.execute(select(RunModel))).scalar_one()
    assert asked == [run.id]
    assert report.niche == niche.NicheReport(run_id=run.id, found=3, added=2)
    assert run.stats["niche"] == {"found": 3, "added": 2}


async def test_failed_niche_collection_does_not_fail_a_paid_run(session: AsyncSession) -> None:
    """Доноры оплачены и сохранены — сбор ниши повторяется кнопкой, а прогон
    не должен стать «сбоем» из-за шага, который только читает базу."""

    async def broken(run_id: int) -> niche.NicheReport:
        raise RuntimeError("база отвалилась на сборе ниши")

    serp = FakeSerp(["https://good.com/a"])
    deps = replace(await _deps(session, serp, _ahrefs({"good.com": GOOD})), niche=broken)

    report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

    run = (await session.execute(select(RunModel))).scalar_one()
    assert run.status is RunStatus.DONE
    assert report.niche is None
    assert run.stats["niche"] == {"failure": "RuntimeError: база отвалилась на сборе ниши"}


def _migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / (
        "backend/migrations/versions/27a07f0ca34b_niche_advertisers.py"
    )
    spec = importlib.util.spec_from_file_location("niche_advertisers_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_column(connection: Connection) -> tuple[bool, bool]:
    def exists() -> bool:
        found = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'advertisers' AND column_name = 'source'"
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
    assert await connection.run_sync(_source_column) == (False, True)


class TestScreen:
    async def test_queue_decide_and_collect_through_the_api(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
    ) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        await session.execute(
            update(DomainModel)
            .where(DomainModel.host == "bookie.example.test")
            .values(judge_quote="Place your bets on football")
        )
        run_id = await _run(session, ["bookie.example.test"])
        await session.commit()
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")

        collected = await client.post(
            f"/api/advertisers/niche/collect?run_id={run_id}", headers=bearer(token)
        )
        shown = await client.get("/api/advertisers/niche", headers=bearer(token))
        card = shown.json()["rows"][0]
        decided = await client.post(
            f"/api/advertisers/niche/{card['id']}/decide",
            json={"write": True},
            headers=bearer(token),
        )
        after = await client.get("/api/advertisers/niche", headers=bearer(token))

        assert collected.json() == {"run_id": run_id, "found": 1, "added": 1}
        assert shown.json()["waiting"] == 1
        assert card["host"] == "bookie.example.test"
        assert card["keywords"] == ["sports betting"]
        assert (card["country"], card["intent_by"]) == ("de", "judge")
        assert card["quote"] == "Place your bets on football"
        assert card["confirmed"] is None
        assert decided.status_code == 200, decided.text
        assert decided.json()["confirmed"] is True
        assert after.json() == {"rows": [], "waiting": 0}
        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.ADVERTISER_REVIEWED)
        )
        assert entry is not None
        assert entry.details is not None
        assert entry.details["решение"] == "пишем"

    async def test_decision_needs_the_reviewer_right_and_unknown_is_404(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await make_user("смотрит@site.com", role=UserRole.OPERATOR, permissions={"prices": False})
        await make_user("админ@site.com", role=UserRole.ADMIN)
        viewer = await sign_in("смотрит@site.com")
        admin = await sign_in("админ@site.com")

        refused = await client.post(
            "/api/advertisers/niche/1/decide", json={"write": True}, headers=bearer(viewer)
        )
        missing = await client.post(
            "/api/advertisers/niche/999999/decide", json={"write": True}, headers=bearer(admin)
        )
        no_run = await client.post(
            "/api/advertisers/niche/collect?run_id=999999", headers=bearer(admin)
        )

        assert refused.status_code == 403
        assert missing.status_code == 404
        assert no_run.status_code == 404


NICHE_ROUTES = {
    ("GET", "/api/advertisers/niche"): "view",
    ("POST", "/api/advertisers/niche/{advertiser_id}/decide"): "prices",
    ("POST", "/api/advertisers/niche/collect"): "prices",
}


async def test_niche_route_table_covers_the_app_and_nobody_without_pass(
    api_app: FastAPI, client: AsyncClient
) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith("/api/advertisers/niche")
        for method in methods
    }
    assert in_app == set(NICHE_ROUTES)
    for method, path in NICHE_ROUTES:
        response = await client.request(method, path.replace("{advertiser_id}", "1"))
        assert response.status_code == 401, path
