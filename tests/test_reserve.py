"""Резерв: не прошедшие прогона — с причиной, и «Взять на рассмотрение».

Вопрос оператора 20.10.2026: прогон на 122 домена предложил 28, а 80 не прошли пороги, —
«куда деваются остальные, можно ли выбирать из них при нехватке». Здесь — прогон целиком
на настоящей базе (провайдер — заглушка) и что из него показывает резерв: слабые метрики,
регион и «не проверен» из-за нехватки данных берутся, «кому не пишем» видно, но не берётся;
взятый — в очереди своего прогона, повтор ничего не задваивает, принятый из резерва получает
адрес и письма.

Выдача прогона — готовыми доменами (`RunRequest.candidates`): домены — на зарезервированных
зонах (`.test`, `.zz`), а нормализация выдачи по списку публичных суффиксов их бы отбросила.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.ahrefs.client import AhrefsClient
from backend.features.contacts.repository import ContactRepository
from backend.features.core.domain import AuditAction, DonorStatus, SuppressionReason, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.run import RunCandidateModel, RunModel, RunVerdictModel
from backend.features.donors.rejudge import ONLY_DR
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.review.candidates import Decision, RunReview, UnknownRunError
from backend.features.review.reserve import Reserve, ReserveFilters, ReserveRow
from backend.features.review.reserve_rules import NOT_TAKEN, Group
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from backend.features.runs.verdicts import GAP
from backend.features.serp.protocol import SerpResult
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import columns_down_and_up, load_migration

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
QUOTA = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}
GOOD = {"domain_rating": 40, "org_traffic": 10_000, "refdomains": 500, "org_keywords": 2000}
#: Выдача прогона: годный, слабый по трафику, чужой по региону, крупный и гос. зона.
METRICS = {
    "maple-fit.test": GOOD,
    "quiet-blog.test": {**GOOD, "org_traffic": 120},
    "far-away.test": GOOD,
    "giant-news.test": {**GOOD, "domain_rating": 92},
}
COUNTRIES = {
    "maple-fit.test": [("ca", 6000), ("us", 3000)],
    "quiet-blog.test": [("ca", 100), ("us", 20)],
    "far-away.test": [("us", 6000), ("gb", 2500), ("in", 600), ("au", 400), ("de", 300)],
}
HOSTS = [*METRICS, "bureau.gov.zz"]


class Serp:
    """Выдача не покупается: прогону передана готовая (`RunRequest.candidates`)."""

    name = "fake"

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        raise AssertionError("выдача передана прогону готовой")


def _ahrefs() -> AhrefsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cost = {"x-api-units-cost-total-actual": "10"}
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            rows = [{"url": f"{h}/", **METRICS[h]} for h in hosts if h in METRICS]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        host = request.url.params["target"]
        rows = [{"country": code, "org_traffic": n} for code, n in COUNTRIES.get(host, [])]
        return httpx.Response(200, json={"metrics": rows}, headers=cost)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


async def _run(
    session: AsyncSession,
    country: str = "ca",
    hosts: Sequence[str] = HOSTS,
    thresholds: Thresholds = T,
) -> RunModel:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        thresholds,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=1,
    )
    deps = RunDeps(
        provider=Serp(),
        client=_ahrefs(),
        donors=DonorRepository(session),
        runs=runs,
        exclusions=Exclusions(session),
        review=RunReview(session),
    )
    found = Candidates(list(hosts), keywords=1, results=len(hosts), empty_keywords=[], dropped=0)
    await execute_run(
        deps, RunRequest(["hockey blog"], country, thresholds, settings.id, candidates=found)
    )
    found = await session.execute(select(RunModel).order_by(RunModel.id.desc()).limit(1))
    return found.scalar_one()


def _by_host(rows: Sequence[ReserveRow]) -> dict[str, ReserveRow]:
    return {row.host: row for row in rows}


async def _domain_id(session: AsyncSession, host: str) -> int:
    found = await session.scalar(select(DomainModel.id).where(DomainModel.host == host))
    assert found is not None
    return found


class TestWhatTheReserveShows:
    async def test_a_run_shows_who_did_not_pass_and_why(self, session: AsyncSession) -> None:
        run = await _run(session)

        page = await Reserve(session).page(ReserveFilters(run_id=run.id))
        rows = _by_host(page.rows)

        assert set(rows) == {"quiet-blog.test", "far-away.test", "giant-news.test", "bureau.gov.zz"}
        assert "maple-fit.test" not in rows, "годный — в очереди, а не в резерве"
        assert rows["quiet-blog.test"].group is Group.WEAK
        assert rows["quiet-blog.test"].reason == "органический трафик 120 ниже 500"
        assert rows["far-away.test"].group is Group.REGION
        assert (rows["far-away.test"].fit.place, rows["far-away.test"].fit.top) == (None, "us")
        # Отсеянному по метрикам стран не покупали: места страны прогона не знаем.
        assert (rows["quiet-blog.test"].fit.asked, rows["quiet-blog.test"].fit.place) == (
            False,
            None,
        )
        assert page.groups == {"weak": 1, "region": 1, "gap": 0, "not_writing": 2}
        assert page.total == 4
        assert page.run is not None
        assert page.run.id == run.id

    async def test_not_writing_is_shown_but_not_taken_and_says_why(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)

        rows = _by_host((await Reserve(session).page(ReserveFilters(run_id=run.id))).rows)

        assert rows["quiet-blog.test"].takeable
        assert rows["far-away.test"].takeable
        assert rows["giant-news.test"].refusal == NOT_TAKEN
        assert rows["giant-news.test"].said == "Кому не пишем: слишком крупный — DR выше 80"
        assert rows["bureau.gov.zz"].refusal == NOT_TAKEN
        assert rows["bureau.gov.zz"].said == "Кому не пишем: гос. или учебная зона"
        assert rows["bureau.gov.zz"].donor is None, "гейт снял его до Ahrefs — метрик нет"

    async def test_filters_by_group_country_and_top_five(self, session: AsyncSession) -> None:
        await _run(session)
        reserve = Reserve(session)

        region = await reserve.page(ReserveFilters(group=Group.REGION))
        with_gb = await reserve.page(ReserveFilters(geo="GB"))
        uk_runs = await reserve.page(ReserveFilters(country="gb"))

        assert [row.host for row in region.rows] == ["far-away.test"]
        assert region.groups["weak"] == 1, "счётчики группы — под остальными фильтрами"
        assert [row.host for row in with_gb.rows] == ["far-away.test"]
        assert uk_runs.total == 0
        assert region.countries == {"ca": 1}
        # Страны топ-5 знают только дошедшие до стран: отсеянный по региону.
        assert region.geos == {"au": 1, "de": 1, "gb": 1, "in": 1, "us": 1}

    async def test_across_runs_a_domain_is_one_row_of_its_latest_run(
        self, session: AsyncSession
    ) -> None:
        first = await _run(session)
        second = await _run(session)

        page = await Reserve(session).page(ReserveFilters())

        assert sorted(row.host for row in page.rows) == [
            "bureau.gov.zz",
            "far-away.test",
            "giant-news.test",
            "quiet-blog.test",
        ]
        assert {row.run_id for row in page.rows} == {second.id}
        assert (await Reserve(session).page(ReserveFilters(run_id=first.id))).total == 4

    async def test_an_unknown_run_is_refused_in_words(self, session: AsyncSession) -> None:
        with pytest.raises(UnknownRunError, match="Прогона №987654 нет"):
            await Reserve(session).page(ReserveFilters(run_id=987654))
        with pytest.raises(UnknownRunError):
            await Reserve(session).page(ReserveFilters(run_id=2**40))


class TestTakingFromTheReserve:
    async def test_taken_lands_in_its_runs_queue_once(self, session: AsyncSession) -> None:
        run = await _run(session)
        quiet = await _domain_id(session, "quiet-blog.test")
        reserve = Reserve(session)

        first = await reserve.take([(run.id, quiet)], by="op@team.example.test")
        again = await reserve.take([(run.id, quiet)], by="op@team.example.test")

        assert (first.taken, first.count) == ({run.id: ["quiet-blog.test"]}, 1)
        assert again.count == 0
        assert again.already == ["quiet-blog.test"]
        rows = (
            (
                await session.execute(
                    select(RunCandidateModel).where(RunCandidateModel.domain_id == quiet)
                )
            )
            .scalars()
            .all()
        )
        assert [(row.run_id, row.status, row.taken_by) for row in rows] == [
            (run.id, "pending", "op@team.example.test")
        ]
        page = await RunReview(session).page(run.id, status=Decision.PENDING, show_doubtful=True)
        reserved = {row.domain.host: row.reserve for row in page.rows}
        assert reserved["quiet-blog.test"] == "органический трафик 120 ниже 500"
        assert reserved["maple-fit.test"] is None, "предложенный прогоном — не из резерва"
        after = _by_host((await reserve.page(ReserveFilters(run_id=run.id))).rows)
        assert after["quiet-blog.test"].refusal == "уже на рассмотрении в этом прогоне"

    async def test_not_writing_and_strangers_are_refused_in_words(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        giant = await _domain_id(session, "giant-news.test")
        fit = await _domain_id(session, "maple-fit.test")

        report = await Reserve(session).take(
            [(run.id, giant), (run.id, fit), (2**40, giant)], by="op@team.example.test"
        )

        assert report.count == 0
        assert report.refused["giant-news.test"] == NOT_TAKEN
        assert report.refused[f"№{fit}"].startswith(f"в резерве прогона №{run.id}")
        assert await Reserve(session).take([], by="op@team.example.test") == type(report)()

    async def test_one_decision_per_domain_is_kept(self, session: AsyncSession) -> None:
        """Принятый, отклонённый и ждущий в другой очереди — в резерве стоят, но не берутся."""
        run = await _run(session)
        donors = {
            host: await session.scalar(
                select(DonorModel).join(DomainModel).where(DomainModel.host == host)
            )
            for host in ("quiet-blog.test", "far-away.test")
        }
        quiet, far = donors["quiet-blog.test"], donors["far-away.test"]
        assert quiet is not None
        assert far is not None
        quiet.review = Decision.ACCEPTED.value
        await session.flush()
        rows = _by_host((await Reserve(session).page(ReserveFilters(run_id=run.id))).rows)
        assert rows["quiet-blog.test"].refusal == "уже донор — принят человеком"

        quiet.review = Decision.REJECTED.value
        other = await _run(session, "gb", ["far-away.test"])
        await session.flush()
        rows = _by_host((await Reserve(session).page(ReserveFilters(run_id=run.id))).rows)
        assert rows["quiet-blog.test"].refusal.startswith("отклонён человеком")  # type: ignore[union-attr]
        assert rows["far-away.test"].refusal == f"ждёт решения в очереди прогона №{other.id}"

    async def test_threshold_in_force_still_says_not_writing(self, session: AsyncSession) -> None:
        """Слабый в этом прогоне, а по последнему замеру — крупный: «кому не пишем» — по
        действующей версии, как у очереди и писем."""
        run = await _run(session)
        quiet = await session.scalar(
            select(DonorModel).join(DomainModel).where(DomainModel.host == "quiet-blog.test")
        )
        assert quiet is not None
        quiet.dr = 95
        await session.flush()

        rows = _by_host((await Reserve(session).page(ReserveFilters(run_id=run.id))).rows)

        assert rows["quiet-blog.test"].refusal == "Не берём: DR > 80 — крупным сайтам не пишем"


class TestAcceptedFromTheReserve:
    async def test_accepted_from_the_reserve_gets_an_address_search(
        self, session: AsyncSession
    ) -> None:
        """Взятый из резерва и принятый — годен словом человека: поиск адреса ставится ему,
        как любому принятому, хотя вердикт прогона — «не подходит»."""
        run = await _run(session)
        far = await _domain_id(session, "far-away.test")
        await Reserve(session).take([(run.id, far)], by="op@team.example.test")
        review = RunReview(session)
        page = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)
        candidate = next(
            row.candidate.id for row in page.rows if row.domain.host == "far-away.test"
        )

        await review.decide(run.id, [candidate], Decision.ACCEPTED, by="op@team.example.test")

        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == far))
        assert donor is not None
        await session.refresh(donor)
        assert (donor.status, donor.review) == (DonorStatus.SUITABLE, Decision.ACCEPTED.value)
        assert await ContactRepository(session).pending_count() >= 1

    async def test_undoing_the_decision_returns_the_measured_verdict(
        self, session: AsyncSession
    ) -> None:
        """Сняли решение о взятом из резерва — слова человека «годен» больше нет: донору снова
        вердикт его последнего замера — здесь замера того прогона, из резерва которого взяли."""
        run = await _run(session)
        far = await _domain_id(session, "far-away.test")
        await Reserve(session).take([(run.id, far)], by="op@team.example.test")
        review = RunReview(session)
        page = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)
        candidate = next(
            row.candidate.id for row in page.rows if row.domain.host == "far-away.test"
        )
        await review.decide(run.id, [candidate], Decision.ACCEPTED, by="op@team.example.test")

        await review.decide(run.id, [candidate], Decision.PENDING, by="op@team.example.test")

        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == far))
        assert donor is not None
        await session.refresh(donor)
        assert (donor.status, donor.reject_reason, donor.review) == (
            DonorStatus.UNSUITABLE,
            "CA не входит в топ-5 и даёт меньше 20%",
            None,
        )


class TestWhatHappenedAfterTheRun:
    async def test_a_stoplisted_domain_is_not_taken_and_says_why(
        self, session: AsyncSession
    ) -> None:
        """Стоп-лист, поставщик, молчание и «не продаёт» появились после прогона — «взять» их
        перепроверяет и отказывает словами; строка экрана говорит то же (ревью 20.10.2026)."""
        run = await _run(session)
        quiet = await _domain_id(session, "quiet-blog.test")
        session.add(SuppressionModel(domain_id=quiet, reason=SuppressionReason.COMPLAINED))
        await session.flush()

        report = await Reserve(session).take([(run.id, quiet)], by="op@team.example.test")
        rows = _by_host((await Reserve(session).page(ReserveFilters(run_id=run.id))).rows)

        assert report.count == 0
        assert report.refused["quiet-blog.test"] == "Не берём: в стоп-листе"
        assert rows["quiet-blog.test"].refusal == "Не берём: в стоп-листе"
        assert rows["far-away.test"].takeable


class TestOneRuleForTheListAndItsCounters:
    async def test_a_count_on_a_filter_is_what_the_filter_shows(
        self, session: AsyncSession
    ) -> None:
        """Домен в резерве двух прогонов показан строкой последнего; фильтры выбирают из этих
        строк, и число у пункта — сколько строк он покажет (ревью 20.10.2026: счётчики
        считали одну строку, а фильтр находил другую)."""
        await _run(session, "ca", ["far-away.test"])
        await _run(session, "fr", ["far-away.test"], replace(T, min_dr=50))
        reserve = Reserve(session)

        everything = await reserve.page(ReserveFilters())
        for group in Group:
            shown = await reserve.page(ReserveFilters(group=group))
            assert shown.total == everything.groups[group.value], group
        for country, count in everything.countries.items():
            assert (await reserve.page(ReserveFilters(country=country))).total == count, country
        for code, count in everything.geos.items():
            assert (await reserve.page(ReserveFilters(geo=code))).total == count, code
        assert everything.total == 1
        assert everything.groups["weak"] == 1
        assert (await reserve.page(ReserveFilters(country="ca"))).total == 0


class TestNotCheckedForLackOfData:
    async def test_a_dr_only_domain_is_its_own_group_and_can_be_taken(
        self, session: AsyncSession
    ) -> None:
        """Отсеянному по DR остальных метрик не покупали; мягче порог пускает его дальше, а
        судить нечем — «не проверен» с причиной, 90 дней свежий. Резерв показывает его своей
        группой, и «взять» работает как для остальных (ревью 20.10.2026)."""
        await _run(session, "ca", ["far-away.test"], replace(T, min_dr=50))
        softer = await _run(session, "ca", ["far-away.test"])
        reserve = Reserve(session)

        page = await reserve.page(ReserveFilters(run_id=softer.id, group=Group.GAP))
        far = await _domain_id(session, "far-away.test")
        report = await reserve.take([(softer.id, far)], by="op@team.example.test")

        assert [row.host for row in page.rows] == ["far-away.test"]
        assert page.rows[0].group is Group.GAP
        assert page.rows[0].said == f"Не проверен: {ONLY_DR}"
        assert page.groups[Group.GAP.value] == 1
        assert report.taken == {softer.id: ["far-away.test"]}


class TestReserveTakenKeepsTheWordOfTheHuman:
    async def test_a_remeasure_does_not_take_back_suitable(self, session: AsyncSession) -> None:
        """Принятый из резерва годен словом человека: перемер другой страной пишет вердикт
        замера, но вердикт для выходов не снимает (ревью 20.10.2026)."""
        run = await _run(session)
        far = await _domain_id(session, "far-away.test")
        await Reserve(session).take([(run.id, far)], by="op@team.example.test")
        review = RunReview(session)
        page = await review.page(run.id, status=Decision.PENDING, show_doubtful=True)
        candidate = next(
            row.candidate.id for row in page.rows if row.domain.host == "far-away.test"
        )
        await review.decide(run.id, [candidate], Decision.ACCEPTED, by="op@team.example.test")
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == far))
        assert donor is not None
        donor.metrics_refreshed_at = datetime.now(UTC) - timedelta(days=91)
        await session.flush()

        await _run(session, "fr", ["far-away.test"])

        await session.refresh(donor)
        assert (donor.status, donor.review) == (DonorStatus.SUITABLE, Decision.ACCEPTED.value)
        assert donor.measured_status == DonorStatus.UNSUITABLE.value


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
    return await sign_in("оператор@team.example.test")


class TestOverHttp:
    async def test_list_take_and_journal(
        self, client: AsyncClient, session: AsyncSession, operator: str
    ) -> None:
        run = await _run(session)
        await session.commit()
        far = await _domain_id(session, "far-away.test")

        listed = await client.get(f"/api/review/reserve?run_id={run.id}", headers=bearer(operator))
        taken = await client.post(
            "/api/review/reserve/take",
            json={"items": [{"run_id": run.id, "domain_id": far}]},
            headers=bearer(operator),
        )

        assert listed.status_code == 200, listed.text
        body = listed.json()
        rows = {row["host"]: row for row in body["rows"]}
        assert (body["total"], body["limit"]) == (4, 20)
        assert body["run"]["id"] == run.id
        assert rows["far-away.test"]["reason"] == "Канада не входит в топ-5 и даёт меньше 20%"
        assert rows["far-away.test"]["fit"] == {
            "place": None,
            "share": None,
            "top": "us",
            "top_share": pytest.approx(0.6),
            "asked": True,
            "partial": False,
        }
        assert (rows["far-away.test"]["refdomains"], rows["far-away.test"]["keywords"]) == (
            500,
            2000,
        )
        assert rows["bureau.gov.zz"]["takeable"] is False
        assert rows["bureau.gov.zz"]["donor_id"] is None, "гейт снял до Ahrefs — карточки нет"
        assert isinstance(rows["far-away.test"]["donor_id"], int)
        assert taken.status_code == 200, taken.text
        assert taken.json() == {
            "taken": 1,
            "runs": {str(run.id): ["far-away.test"]},
            "already": [],
            "refused": {},
        }
        record = (
            await session.execute(
                select(AuditLogModel).where(AuditLogModel.target == f"run:{run.id}")
            )
        ).scalar_one()
        assert record.action is AuditAction.USER_UPDATED
        assert record.details == {
            "действие": "взят из резерва на рассмотрение",
            "доменов": 1,
            "домены": ["far-away.test"],
        }

    async def test_taking_needs_the_review_right(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        run = await _run(session)
        await session.commit()
        await make_user(
            "смотрит@team.example.test", role=UserRole.OPERATOR, permissions={"prices": False}
        )
        token = await sign_in("смотрит@team.example.test")

        listed = await client.get("/api/review/reserve", headers=bearer(token))
        taken = await client.post(
            "/api/review/reserve/take",
            json={"items": [{"run_id": run.id, "domain_id": 1}]},
            headers=bearer(token),
        )

        assert listed.status_code == 200
        assert taken.status_code == 403
        assert (await session.execute(select(RunCandidateModel.taken_by))).scalars().all() == [
            None
        ], "в очереди — только предложенный прогоном"

    async def test_unknown_run_is_a_404_in_words(self, client: AsyncClient, operator: str) -> None:
        response = await client.get("/api/review/reserve?run_id=987654", headers=bearer(operator))

        assert response.status_code == 404
        assert "Прогона №987654 нет" in response.text


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


def _columns_down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    return columns_down_and_up(
        connection, "aa72439e27d1_reserve_taken.py", "run_candidates", {"taken_by", "taken_at"}
    )


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    """Откат снимает пометку «взят из резерва» — взятые остаются обычными кандидатами;
    подъём возвращает обе колонки."""
    connection = await session.connection()
    down, up = await connection.run_sync(_columns_down_and_up)

    assert (down, up) == (set(), {"taken_by", "taken_at"})


def _gaps_down_and_up(connection: Connection) -> dict[str, str | None]:
    """Вниз и вверх; код причины у строк вердикта после подъёма — по причине словами."""
    migration = load_migration("aa72439e27d1_reserve_taken.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        migration.upgrade()
    rows = connection.execute(text("SELECT reason, cause FROM run_verdicts"))
    return {str(reason): cause for reason, cause in rows.tuples()}


async def test_the_revision_names_a_data_gap_written_without_a_cause(
    session: AsyncSession,
) -> None:
    """Код вердикта прогона писал «не проверен» без кода причины (ревью 20.10.2026): такая
    строка в группу «не хватает данных» не попала бы. Подъём ставит ей `gap` — по причине из
    того же списка, что пишет код; «не проверен» по другой причине и отказ кода не получают."""
    run = await _run(session, "ca", ["far-away.test"])
    domains = [
        DomainModel(host=host) for host in ("gap-left.test", "unknown-left.test", "weak-left.test")
    ]
    session.add_all(domains)
    await session.flush()
    session.add_all(
        [
            RunVerdictModel(
                run_id=run.id, domain_id=domains[0].id, status="unchecked", reason=ONLY_DR
            ),
            RunVerdictModel(
                run_id=run.id,
                domain_id=domains[1].id,
                status="unchecked",
                reason="Ahrefs не знает домен",
            ),
            RunVerdictModel(
                run_id=run.id, domain_id=domains[2].id, status="unsuitable", reason="DR 9 ниже 20"
            ),
        ]
    )
    await session.flush()
    connection = await session.connection()

    causes = await connection.run_sync(_gaps_down_and_up)

    assert causes[ONLY_DR] == GAP
    assert causes["Ahrefs не знает домен"] is None
    assert causes["DR 9 ниже 20"] is None
