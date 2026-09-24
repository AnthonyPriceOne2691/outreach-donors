"""Экран писем по этапам: очередь, сборка и правка оффера рекламодателю по HTTP.

Ядро Этапа 2 проверено в `test_letters_advertisers.py`; здесь — то, что
видит и присылает экран: у каждого этапа своя очередь, свой текст
по умолчанию и своя воронка, этап доходит до задачи, а прогоны у
рекламодателей — отказ у формы, а не молчание в задаче.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.letters import compose, draft, template
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.workers import jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer, make_donor
from tests.test_letter_draft import FakeQueue
from tests.test_letters_advertisers import (
    ANCHOR,
    DONOR,
    PAGE,
    FakeRewriter,
    build_offers,
    make_advertiser,
    offers,
    priced_donor,
)

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


def _offer_letter(**changes: str) -> dict[str, Any]:
    shown = draft.default_draft(Stage.ADVERTISERS)
    zones = {zone.name: zone.text for zone in shown.zones}
    zones.update(changes)
    return {"subject": shown.subject, "zones": zones}


async def _queued_offer(session: AsyncSession) -> int:
    await priced_donor(session)
    await make_advertiser(session, "brand.example.test")
    await build_offers(session)
    await session.commit()
    (letter,) = await offers(session)
    return letter.id


class TestQueuePerStage:
    async def test_each_stage_sees_its_own_letters(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        """Вопрос донору о цене и оффер рекламодателю читаются разными
        глазами — в одном списке человек согласовал бы не то."""
        await _queued_offer(session)
        await make_donor(session, "donor-two.example.test", email="ed@donor-two.example.test")
        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Доноры")
        )
        await session.commit()

        donors = (await client.get("/api/letters", headers=bearer(admin_token))).json()
        advertisers = (
            await client.get("/api/letters?stage=advertisers", headers=bearer(admin_token))
        ).json()

        assert donors["stage"] == "donors"
        assert [row["host"] for row in donors["letters"]] == ["donor-two.example.test"]
        assert advertisers["stage"] == "advertisers"
        assert [row["host"] for row in advertisers["letters"]] == ["brand.example.test"]

    async def test_offer_screen_gets_its_own_text_and_funnel(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        shown = (
            await client.get("/api/letters?stage=advertisers", headers=bearer(admin_token))
        ).json()

        assert shown["letter_default"]["subject"] == "Your placement on {{donor_host}}"
        assert "цена донора свежая" in shown["funnel"]
        offer = next(z for z in shown["letter_default"]["zones"] if z["name"] == "offer")
        assert offer["kind"] == "fixed"
        assert "{{anchor}}" in offer["text"]

    async def test_offer_card_shows_the_reminders_that_will_go(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        """Согласуя оффер, человек согласует цепочку: напоминания об оффере
        и темой первого письма — ровно как они уйдут."""
        await _queued_offer(session)

        card = (
            await client.get("/api/letters?stage=advertisers", headers=bearer(admin_token))
        ).json()["letters"][0]

        assert f'"{ANCHOR}"' in card["body"]
        assert PAGE in card["body"]
        assert [f["subject"] for f in card["followups"]] == [f"Your placement on {DONOR}"] * 2
        assert "sponsored placement of yours" in card["followups"][0]["body"]


class TestBuildingOffers:
    async def test_stage_travels_to_the_job(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Сентябрь", "stage": "advertisers"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200
        assert queue.kwargs[0]["stage"] == "advertisers"

    async def test_runs_are_refused_at_the_form(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        """Отказ из задачи человек увидел бы через минуты и не у формы."""
        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Сентябрь", "stage": "advertisers", "run_ids": [1]},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "не по прогонам" in response.json()["detail"]
        assert queue.kwargs == []

    async def test_edited_offer_is_checked_as_an_offer(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue
    ) -> None:
        """Анкор, перенесённый во вступление, ушёл бы пересказанным моделью."""
        moved = _offer_letter(opening='Your "{{anchor}}" link caught my eye last week.')

        response = await client.post(
            "/api/letters/build",
            json={"campaign": "Сентябрь", "stage": "advertisers", "letter": moved},
            headers=bearer(admin_token),
        )

        assert response.status_code == 400
        assert "переписываемой зоне" in response.json()["detail"]
        assert queue.kwargs == []

    async def test_same_name_on_the_other_stage_is_another_campaign(
        self, client: AsyncClient, admin_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        session.add(CampaignModel(stage=Stage.DONORS, name="Сентябрь", status="draft"))
        await session.commit()

        response = await client.post(
            "/api/letters/build",
            json={
                "campaign": "Сентябрь",
                "stage": "advertisers",
                "letter": _offer_letter(terms="No retainer, you pay per article."),
            },
            headers=bearer(admin_token),
        )

        assert response.status_code == 200
        assert "No retainer, you pay per article." in queue.kwargs[0]["letter_template"]


class TestEditingAnOffer:
    async def test_difference_is_measured_against_the_offer(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        """Правка, вернувшая оффер к шаблону, — ноль отличия. Отмеренная
        от вопроса донору о цене, она дала бы число ни о чём."""
        letter_id = await _queued_offer(session)
        link = compose.FoundLink(donor_host=DONOR, page_url=PAGE, anchor=ANCHOR)
        plain = compose.render(
            template.advertiser(), compose.values_for(host="brand.example.test", link=link)
        )

        response = await client.patch(
            f"/api/letters/{letter_id}",
            json={"subject": plain.subject, "body": plain.body},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200
        assert response.json()["uniqueness"] == 0.0

    async def test_removed_advertiser_is_not_edited(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, filled_legal: None
    ) -> None:
        letter_id = await _queued_offer(session)
        row = await session.scalar(select(AdvertiserModel))
        await session.delete(row)
        await session.commit()

        response = await client.patch(
            f"/api/letters/{letter_id}",
            json={"subject": "S", "body": "B"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "сняли" in response.json()["detail"]


class _Closable:
    async def aclose(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _ClosableRewriter(FakeRewriter):
    async def aclose(self) -> None:
        return None


class TestTheJob:
    """Тело задачи — на настоящей базе: до 24.09.2026 задачу прогона
    не исполнял ни один тест, и она падала на первой строке."""

    async def test_queued_build_of_offers_writes_offers(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await priced_donor(session)
        await make_advertiser(session, "brand.example.test")
        monkeypatch.setattr(jobs, "RewriteClient", _ClosableRewriter)
        monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Closable())
        monkeypatch.setattr(
            jobs,
            "async_sessionmaker",
            lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
        )

        report = await jobs._build_letters(
            "Сентябрь", "za", (), 10, (), None, (), Stage.ADVERTISERS
        )

        assert report["prepared"] == 1
        assert report["funnel"]["цена донора свежая"] == 1
        (letter,) = await offers(session)
        assert letter.idempotency_key == "advertisers:brand.example.test:0"

    def test_stage_arrives_as_a_string_and_defaults_to_donors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Задача живёт в очереди дольше версии кода: поставленная до этапов
        приходит без него и остаётся Этапом 1."""
        seen: list[Stage] = []

        async def build(*args: object) -> dict[str, Any]:
            seen.append(args[-1])  # type: ignore[arg-type]
            return {}

        monkeypatch.setattr(jobs, "_build_letters", build)
        monkeypatch.setattr(jobs, "check_storage", lambda: None)

        jobs.build_letter_queue("Май")
        jobs.build_letter_queue("Сентябрь", stage="advertisers")

        assert seen == [Stage.DONORS, Stage.ADVERTISERS]
