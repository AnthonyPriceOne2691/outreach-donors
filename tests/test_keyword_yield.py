"""Что дали ключи: по каким запросам нашлись принятые доноры.

На настоящей базе: связь «домен → ключи» лежит в прогоне, решения — в его
очереди и на доноре, и ошибиться здесь легко в счёте, а не в формуле:
домен, найденный одним ключом в трёх прогонах, — один домен, а не три.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from backend.features.core.domain import DonorStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.review.candidates import Decision, RunReview
from backend.features.review.keyword_yield import country_yield, run_yield
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _run(
    session: AsyncSession,
    keywords: list[str],
    found_by: dict[str, list[str]] | None,
    *,
    country: str = "us",
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
        keywords=keywords,
        country=country,
        status=RunStatus.DONE,
    )
    run.candidates = {"hosts": list(found_by or {}), "found_by": found_by} if found_by else {}
    await session.flush()
    return run


async def _decide(session: AsyncSession, run: RunModel, decisions: dict[str, Decision]) -> None:
    review = RunReview(session)
    await review.queue_run(run.id, list(decisions))
    rows = await session.execute(
        select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(list(decisions)))
    )
    ids = dict(rows.tuples().all())
    page = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)
    by_host = {row.domain.host: row.candidate.id for row in page.rows}
    for host, decision in decisions.items():
        if decision is not Decision.PENDING and host in ids:
            await review.decide(run.id, [by_host[host]], decision, by="anna@parsingprices.com")
    await session.flush()


class TestRunYield:
    async def test_counts_what_each_keyword_brought(self, session: AsyncSession) -> None:
        for host in ("blog-a.test", "blog-b.test", "brand.test"):
            await make_donor(session, host, review=None)
        run = await _run(
            session,
            ["write for us", "best crm", "empty key"],
            {
                "blog-a.test": ["write for us"],
                "blog-b.test": ["write for us", "best crm"],
                "brand.test": ["best crm"],
                # Отсеян порогами: в выдаче был, до рассмотрения не дошёл.
                "vendor.test": ["best crm"],
            },
        )
        await _decide(
            session,
            run,
            {
                "blog-a.test": Decision.ACCEPTED,
                "blog-b.test": Decision.PENDING,
                "brand.test": Decision.REJECTED,
            },
        )

        rows = {row.keyword: row for row in await run_yield(session, run) or []}

        assert (rows["write for us"].found, rows["write for us"].accepted) == (2, 1)
        assert rows["write for us"].pending == 1
        best = rows["best crm"]
        assert (best.found, best.queued, best.accepted, best.rejected, best.pending) == (
            3,
            2,
            0,
            1,
            1,
        )
        # Пустой ключ — тоже ответ: он в списке с нулями, а не пропал.
        assert (rows["empty key"].found, rows["empty key"].queued) == (0, 0)

    async def test_best_keyword_comes_first(self, session: AsyncSession) -> None:
        for host in ("a.test", "b.test"):
            await make_donor(session, host, review=None)
        run = await _run(session, ["weak", "strong"], {"a.test": ["weak"], "b.test": ["strong"]})
        await _decide(session, run, {"a.test": Decision.REJECTED, "b.test": Decision.ACCEPTED})

        rows = await run_yield(session, run) or []

        assert [row.keyword for row in rows] == ["strong", "weak"]

    async def test_run_before_the_mapping_is_unknown_not_empty(self, session: AsyncSession) -> None:
        """Прогоны до 23.09.2026 связь не хранили: это «не знаем», а не нули."""
        run = await _run(session, ["write for us"], None)

        assert await run_yield(session, run) is None


class TestCountryYield:
    async def test_same_domain_in_three_runs_counts_once(self, session: AsyncSession) -> None:
        """Удачный ключ, повторённый трижды, не должен выглядеть втрое удачнее."""
        await make_donor(session, "blog.test", review="accepted")
        # Один ключ, написанный по-разному: регистр и пробелы — не другой ключ.
        for spelling in ("Write for us", "write  for us", "WRITE FOR US"):
            await _run(session, [spelling], {"blog.test": [spelling]})

        (row,) = await country_yield(session, "us")

        assert (row.found, row.accepted, row.runs) == (1, 1, 3)
        assert row.keyword == "Write for us"

    async def test_only_keywords_with_accepted_donors_and_only_this_country(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, "us-blog.test", review="accepted")
        await make_donor(session, "us-brand.test", review="rejected")
        await make_donor(session, "de-blog.test", review="accepted")
        await _run(session, ["good", "bad"], {"us-blog.test": ["good"], "us-brand.test": ["bad"]})
        await _run(session, ["gut"], {"de-blog.test": ["gut"]}, country="de")

        rows = await country_yield(session, "us")

        assert [row.keyword for row in rows] == ["good"]

    async def test_last_decision_wins(self, session: AsyncSession) -> None:
        """Человек передумал в следующем прогоне — считается нынешнее решение."""
        domain = await make_donor(session, "blog.test", review="accepted")
        await _run(session, ["key"], {"blog.test": ["key"]})
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
        assert donor is not None
        donor.review = "rejected"
        await session.flush()

        assert await country_yield(session, "us") == []

    async def test_unsuitable_domains_never_reached_a_human(self, session: AsyncSession) -> None:
        domain = await make_donor(session, "weak.test", review="accepted")
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
        assert donor is not None
        donor.status = DonorStatus.UNSUITABLE
        await _run(session, ["key"], {"weak.test": ["key"]})

        assert await country_yield(session, "us") == []


class TestYieldOverHttp:
    async def test_review_screen_gets_the_run_yield(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, session: AsyncSession
    ) -> None:
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")
        await make_donor(session, "blog.test", review=None)
        run = await _run(session, ["write for us"], {"blog.test": ["write for us"]})
        await _decide(session, run, {"blog.test": Decision.PENDING})
        await session.commit()

        view = (await client.get(f"/api/review/runs/{run.id}", headers=bearer(token))).json()

        assert view["keywords"] == [
            {
                "keyword": "write for us",
                "found": 1,
                "queued": 1,
                "accepted": 0,
                "rejected": 0,
                "pending": 1,
                "runs": 1,
            }
        ]

    async def test_launch_form_gets_the_country_yield(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, session: AsyncSession
    ) -> None:
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")
        await make_donor(session, "blog.test", review="accepted")
        await _run(session, ["write for us"], {"blog.test": ["write for us"]})
        await session.commit()

        rows = (await client.get("/api/keywords/yield?country=US", headers=bearer(token))).json()

        assert [(row["keyword"], row["accepted"]) for row in rows] == [("write for us", 1)]
