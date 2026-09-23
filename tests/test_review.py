"""Прогон кончается очередью на рассмотрение: принять или отклонить.

Прогон 23.09.2026 (100 ключей US) признал годными по порогам microsoft.com,
x.com, reddit.com и nih.gov, и первые письма пробной сборки ушли бы им на
`copyright@` и `weee@`. Здесь проверяется, что между порогами и письмом
стоит решение человека, что судья сортирует, а не решает, и что его
точность против человека считается.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import ContactStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.letters.building import LetterScopeError, run_scope
from backend.features.letters.repository import LetterRepository
from backend.features.review.candidates import (
    AUTO_ACCEPT_MIN_DECISIONS,
    Decision,
    NotInRunError,
    RunReview,
    Tier,
)
from backend.features.runs.exclusions import ExclusionReason, Exclusions, by_name
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


async def _run(
    session: AsyncSession,
    *,
    country: str = "us",
    found_by: dict[str, list[str]] | None = None,
) -> RunModel:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["saas blog write for us"],
        country=country,
        status=RunStatus.DONE,
    )
    run.candidates = {"hosts": list(found_by or {}), "found_by": found_by or {}}
    await session.flush()
    return run


async def _judged(
    session: AsyncSession,
    host: str,
    recommendation: str | None,
    *,
    decided_by: str = "model",
    intent: str = "editorial_ads",
    review: str | None = None,
) -> DomainModel:
    domain = await make_donor(session, host, review=review)
    domain.judge_recommendation = recommendation
    domain.judge_decided_by = decided_by if recommendation else None
    domain.site_intent = intent if recommendation else None
    await session.flush()
    return domain


async def _candidate_ids(session: AsyncSession, run_id: int) -> dict[str, int]:
    rows = await session.execute(
        select(DomainModel.host, RunCandidateModel.id)
        .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
        .where(RunCandidateModel.run_id == run_id)
    )
    return dict(rows.tuples().all())


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    row = await session.execute(
        select(DonorModel).join(DomainModel).where(DomainModel.host == host)
    )
    donor = row.scalar_one()
    await session.refresh(donor)
    return donor


class TestQueue:
    async def test_suitable_hosts_wait_for_a_human(self, session: AsyncSession) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")

        report = await RunReview(session).queue_run(run.id, ["blog.test", "unknown.test"])

        assert report.pending == 1
        assert (await _candidate_ids(session, run.id)).keys() == {"blog.test"}

    async def test_decided_domain_is_not_reviewed_twice(self, session: AsyncSession) -> None:
        """Решённый раньше домен приходит с тем же решением: смотреть одно
        и то же дважды — работа, которую человек бросит."""
        run = await _run(session)
        await _judged(session, "old.test", "accept", review="accepted")

        report = await RunReview(session).queue_run(run.id, ["old.test"])

        assert report.pending == 0
        assert report.carried == {"accepted": 1}

    async def test_second_queue_of_the_same_run_adds_nothing(self, session: AsyncSession) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)

        await review.queue_run(run.id, ["blog.test"])
        again = await review.queue_run(run.id, ["blog.test"])

        assert again.pending == 0


class TestDecisions:
    async def test_accept_marks_the_donor_and_names_it_for_contacts(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(run.id, ["blog.test"])
        ids = await _candidate_ids(session, run.id)

        report = await review.decide(run.id, [ids["blog.test"]], Decision.ACCEPTED, by="a@b.c")

        assert report.accepted_domains
        donor = await _donor(session, "blog.test")
        assert (donor.review, donor.review_by) == ("accepted", "a@b.c")

    async def test_rejected_domain_is_cut_before_ahrefs_next_time(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        await _judged(session, "brand.test", "reject")
        review = RunReview(session)
        await review.queue_run(run.id, ["brand.test"])
        ids = await _candidate_ids(session, run.id)

        await review.decide(run.id, [ids["brand.test"]], Decision.REJECTED, by="a@b.c")

        excluded = await Exclusions(session).excluded_hosts(["brand.test"], now=NOW)
        assert excluded == {"brand.test": ExclusionReason.REJECTED}

    async def test_undo_restores_the_decision_from_another_run(self, session: AsyncSession) -> None:
        """Отмена не стирает решение по домену целиком: в прошлом прогоне
        человек его принял, и это решение возвращается."""
        first, second = await _run(session), await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(first.id, ["blog.test"])
        first_ids = await _candidate_ids(session, first.id)
        await review.decide(first.id, [first_ids["blog.test"]], Decision.ACCEPTED, by="a", now=NOW)
        await review.queue_run(second.id, ["blog.test"])
        second_ids = await _candidate_ids(session, second.id)
        await review.decide(
            second.id,
            [second_ids["blog.test"]],
            Decision.REJECTED,
            by="b",
            now=NOW + timedelta(days=1),
        )

        await review.decide(second.id, [second_ids["blog.test"]], Decision.PENDING, by="b")

        donor = await _donor(session, "blog.test")
        assert (donor.review, donor.review_by) == ("accepted", "a")

    async def test_undo_of_the_only_decision_leaves_the_donor_undecided(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(run.id, ["blog.test"])
        ids = await _candidate_ids(session, run.id)
        await review.decide(run.id, [ids["blog.test"]], Decision.REJECTED, by="a")

        await review.decide(run.id, [ids["blog.test"]], Decision.PENDING, by="a")

        assert (await _donor(session, "blog.test")).review is None

    async def test_candidates_of_another_run_are_refused(self, session: AsyncSession) -> None:
        first, second = await _run(session), await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(first.id, ["blog.test"])
        ids = await _candidate_ids(session, first.id)

        with pytest.raises(NotInRunError, match=str(ids["blog.test"])):
            await review.decide(second.id, [ids["blog.test"]], Decision.ACCEPTED, by="a")


class TestTiers:
    async def test_judge_sorts_and_hides_doubtful_but_never_drops(
        self, session: AsyncSession
    ) -> None:
        """Судья ошибается (23.09 он отрезал airanklab.com, который прямо
        продаёт публикации), поэтому совет «отказ» скрыт, а не выброшен."""
        run = await _run(session)
        await _judged(session, "likely.test", "accept")
        await _judged(session, "open.test", "review")
        await _judged(session, "unjudged.test", None)
        await _judged(session, "doubtful.test", "reject")
        review = RunReview(session)
        await review.queue_run(
            run.id, ["likely.test", "open.test", "unjudged.test", "doubtful.test"]
        )

        page = await review.page(run.id, status=Decision.PENDING)
        everything = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)

        assert page.rows[0].domain.host == "likely.test"
        assert "doubtful.test" not in [row.domain.host for row in page.rows]
        assert page.hidden == 1
        assert everything.rows[-1].domain.host == "doubtful.test"
        assert everything.rows[-1].tier is Tier.DOUBTFUL

    async def test_big_sites_go_to_the_end_of_their_tier(self, session: AsyncSession) -> None:
        """Полка крупных сайтов: судья честно зовёт forbes.com изданием,
        но гостевой пост ему не продашь — первым в очереди он не стоит."""
        run = await _run(session)
        big = await _judged(session, "forbes.test", "accept")
        small = await _judged(session, "niche-blog.test", "accept")
        for domain, dr in ((big, 94), (small, 45)):
            donor = await _donor(session, domain.host)
            donor.dr = dr
        await session.flush()
        review = RunReview(session)
        await review.queue_run(run.id, ["forbes.test", "niche-blog.test"])

        page = await review.page(run.id, status=Decision.PENDING)

        assert [row.domain.host for row in page.rows] == ["niche-blog.test", "forbes.test"]

    async def test_row_knows_which_keywords_found_it(self, session: AsyncSession) -> None:
        run = await _run(session, found_by={"blog.test": ["saas blog write for us"]})
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(run.id, ["blog.test"])

        page = await review.page(run.id, status=Decision.PENDING)

        assert page.rows[0].found_by == ["saas blog write for us"]


class TestAccuracy:
    async def test_judge_is_scored_against_the_human(self, session: AsyncSession) -> None:
        run = await _run(session)
        await _judged(session, "right.test", "accept", decided_by="model")
        await _judged(session, "wrong.test", "reject", decided_by="arbiter", intent="sells_own")
        await _judged(session, "asked.test", "review")
        await _judged(session, "blind.test", None)
        review = RunReview(session)
        hosts = ["right.test", "wrong.test", "asked.test", "blind.test"]
        await review.queue_run(run.id, hosts)
        ids = await _candidate_ids(session, run.id)
        await review.decide(run.id, [ids[h] for h in hosts], Decision.ACCEPTED, by="a")

        accuracy = await review.accuracy()

        assert accuracy.decided == 4
        assert accuracy.by_advice["accept"].precision == 1.0
        assert accuracy.by_advice["reject"].precision == 0.0
        # Систематическая ошибка видна строкой: арбитр режет продающих своё.
        assert accuracy.by_intent["sells_own"]["reject"].agreed == 0
        assert accuracy.by_layer["arbiter"]["reject"].advised == 1
        assert (accuracy.unjudged, accuracy.asked_to_review) == (1, 1)
        assert not accuracy.auto_accept_ready

    async def test_carried_decisions_do_not_count(self, session: AsyncSession) -> None:
        """Перенесённое решение — копия, а не второе мнение человека."""
        run = await _run(session)
        await _judged(session, "old.test", "accept", review="accepted")
        await RunReview(session).queue_run(run.id, ["old.test"])

        assert (await RunReview(session).accuracy()).decided == 0

    def test_auto_accept_needs_a_real_sample(self) -> None:
        assert AUTO_ACCEPT_MIN_DECISIONS >= 200


class TestExclusionsByName:
    def test_only_undisputed_classes_are_cut_by_name(self) -> None:
        """По имени — только зона и платформа. «Похоже на бренд» сюда не
        идёт: правило судьи отрезало cloudways.com со страницей для авторов."""
        found = by_name(["nih.gov", "cornell.edu", "reddit.com", "substack.com", "airanklab.com"])

        assert found == {
            "nih.gov": ExclusionReason.PUBLIC_ZONE,
            "cornell.edu": ExclusionReason.PUBLIC_ZONE,
            "reddit.com": ExclusionReason.PLATFORM,
            "substack.com": ExclusionReason.PLATFORM,
        }


class TestLetterScope:
    async def test_runs_of_different_countries_are_refused(self, session: AsyncSession) -> None:
        us, za = await _run(session, country="us"), await _run(session, country="za")

        with pytest.raises(LetterScopeError, match="разных стран"):
            await run_scope(LetterRepository(session), [us.id, za.id])

    async def test_contacts_still_searched_block_the_build(self, session: AsyncSession) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")
        review = RunReview(session)
        await review.queue_run(run.id, ["blog.test"])
        ids = await _candidate_ids(session, run.id)
        await review.decide(run.id, [ids["blog.test"]], Decision.ACCEPTED, by="a")

        with pytest.raises(LetterScopeError, match="Контакты ещё не искали у 1"):
            await run_scope(LetterRepository(session), [run.id])

    async def test_only_accepted_of_the_chosen_runs_are_written_to(
        self, session: AsyncSession
    ) -> None:
        chosen, other = await _run(session), await _run(session)
        for host in ("in.test", "rejected.test", "elsewhere.test"):
            await make_donor(session, host, email=f"info@{host}", review=None)
        review = RunReview(session)
        await review.queue_run(chosen.id, ["in.test", "rejected.test"])
        await review.queue_run(other.id, ["elsewhere.test"])
        chosen_ids = await _candidate_ids(session, chosen.id)
        other_ids = await _candidate_ids(session, other.id)
        await review.decide(chosen.id, [chosen_ids["in.test"]], Decision.ACCEPTED, by="a")
        await review.decide(chosen.id, [chosen_ids["rejected.test"]], Decision.REJECTED, by="a")
        await review.decide(other.id, [other_ids["elsewhere.test"]], Decision.ACCEPTED, by="a")
        for host in ("in.test", "elsewhere.test"):
            donor = await _donor(session, host)
            donor.contact_status = ContactStatus.FOUND
            donor.contact_attempted_at = NOW
        await session.flush()

        picked = await LetterRepository(session).candidates(
            Stage.DONORS, limit=10, run_ids=[chosen.id]
        )

        assert [c.host for c in picked] == ["in.test"]


class FakeJob:
    id = "job-из-теста"


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def enqueue(self, *args: Any, **_: Any) -> FakeJob:
        self.calls.append(args)
        return FakeJob()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.review.routes.runs_queue", lambda: fake)
    monkeypatch.setattr("backend.api.review.routes.remember_contacts_job", lambda _: None)
    return fake


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("смотрит@site.com", role=UserRole.OPERATOR, permissions={"prices": False})
    return await sign_in("смотрит@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


#: Маршрут, право, тело. Смотреть — `view`, решать — `prices`.
ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/review/runs/{run}", None, "view"),
    (
        "POST",
        "/api/review/runs/{run}/decide",
        {"candidate_ids": [1], "decision": "accepted"},
        "prices",
    ),
    ("GET", "/api/review/accuracy", None, "view"),
]


class TestApi:
    async def test_table_covers_every_route(self, api_app: Any) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/review")
            for method in methods
        }
        in_table = {(method, path.replace("{run}", "{run_id}")) for method, path, _, _ in ROUTES}
        assert in_app == in_table

    async def test_accepting_queues_the_contact_search(
        self, client: AsyncClient, operator_token: str, session: AsyncSession, queue: FakeQueue
    ) -> None:
        run = await _run(session)
        await _judged(session, "blog.test", "accept")
        await RunReview(session).queue_run(run.id, ["blog.test"])
        await session.commit()
        ids = await _candidate_ids(session, run.id)

        response = await client.post(
            f"/api/review/runs/{run.id}/decide",
            json={"candidate_ids": [ids["blog.test"]], "decision": "accepted"},
            headers=bearer(operator_token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == 1
        assert len(queue.calls) == 1

    async def test_rejecting_queues_nothing(
        self, client: AsyncClient, operator_token: str, session: AsyncSession, queue: FakeQueue
    ) -> None:
        run = await _run(session)
        await _judged(session, "brand.test", "reject")
        await RunReview(session).queue_run(run.id, ["brand.test"])
        await session.commit()
        ids = await _candidate_ids(session, run.id)

        response = await client.post(
            f"/api/review/runs/{run.id}/decide",
            json={"candidate_ids": [ids["brand.test"]], "decision": "rejected"},
            headers=bearer(operator_token),
        )

        assert response.status_code == 200
        assert queue.calls == []

    async def test_viewer_sees_but_does_not_decide(
        self, client: AsyncClient, viewer_token: str, session: AsyncSession, queue: FakeQueue
    ) -> None:
        run = await _run(session)
        await session.commit()

        seen = await client.get(f"/api/review/runs/{run.id}", headers=bearer(viewer_token))
        decided = await client.post(
            f"/api/review/runs/{run.id}/decide",
            json={"candidate_ids": [1], "decision": "accepted"},
            headers=bearer(viewer_token),
        )

        assert seen.status_code == 200
        assert decided.status_code == 403

    async def test_unknown_run_is_a_404(self, client: AsyncClient, operator_token: str) -> None:
        response = await client.get("/api/review/runs/999999", headers=bearer(operator_token))
        assert response.status_code == 404
