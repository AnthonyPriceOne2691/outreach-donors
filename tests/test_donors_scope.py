"""Кто донор: экран, его счётчики, выгрузка и главная — только принятые человеком.

Решение 26.09.2026 (`donors/standing.py`). Запись в `donors` есть у каждого
домена, за чьи метрики заплатил прогон; донором домен становится, когда его
принимает человек на рассмотрении прогона. Проверяется на настоящей базе
и настоящим путём решения (`RunReview.decide`): кандидат без решения,
отклонённый, принятый и принятый, а потом возвращённый в «предложен» —
кто из них виден в списке, в счётчиках фильтров, в выгрузке и на главной.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.ops.overview import overview
from backend.features.review.candidates import Decision, RunReview
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


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
        stage=Stage.DONORS, settings_id=settings.id, keywords=["garden blog"], country="us"
    )


async def _checked(session: AsyncSession, host: str, *, geo: str) -> DomainModel:
    """Проверенный домен: метрики куплены, адрес найден — решения ещё нет."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(
        DonorModel(
            domain_id=domain.id,
            status=DonorStatus.SUITABLE,
            dr=40,
            org_traffic=50_000,
            geo=geo,
            metrics_refreshed_at=NOW - timedelta(days=3),
            contact_status=ContactStatus.FOUND,
            contact_attempted_at=NOW - timedelta(days=3),
        )
    )
    session.add(ContactModel(domain_id=domain.id, email=f"ads@{host}", source=ContactSource.PAGE))
    await session.flush()
    return domain


@pytest.fixture
async def four(session: AsyncSession) -> dict[str, DonorModel]:
    """Четыре кандидата одного прогона — четыре судьбы, решённые настоящим путём."""
    run = await _run(session)
    hosts = {
        "undecided": ("undecided.example.test", "us"),
        "rejected": ("rejected.example.test", "de"),
        "accepted": ("accepted.example.test", "gb"),
        "returned": ("returned.example.test", "fr"),
    }
    domains = {key: await _checked(session, host, geo=geo) for key, (host, geo) in hosts.items()}
    candidates = {}
    for key, domain in domains.items():
        candidate = RunCandidateModel(run_id=run.id, domain_id=domain.id, status="pending")
        session.add(candidate)
        candidates[key] = candidate
    await session.flush()

    review = RunReview(session)
    await review.decide(run.id, [candidates["rejected"].id], Decision.REJECTED, by="тест")
    await review.decide(
        run.id,
        [candidates["accepted"].id, candidates["returned"].id],
        Decision.ACCEPTED,
        by="тест",
    )
    # Вернули в «предложен»: решение снято, донор снова кандидат.
    await review.decide(run.id, [candidates["returned"].id], Decision.PENDING, by="тест")
    await session.commit()

    return {
        key: (
            await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
        ).scalar_one()
        for key, domain in domains.items()
    }


def _rows(body: bytes) -> list[dict[str, str]]:
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";")
    header, *rows = list(reader)
    return [dict(zip(header, row, strict=True)) for row in rows]


async def test_the_decisions_landed_where_expected(four: dict[str, DonorModel]) -> None:
    """Оснастка: решения легли на доноров тем же путём, что с экрана."""
    assert {key: donor.review for key, donor in four.items()} == {
        "undecided": None,
        "rejected": "rejected",
        "accepted": "accepted",
        "returned": None,
    }


async def test_list_shows_only_the_accepted(
    client: AsyncClient, token: str, four: dict[str, DonorModel]
) -> None:
    body = (await client.get("/api/donors", headers=bearer(token))).json()

    assert [row["host"] for row in body["rows"]] == ["accepted.example.test"]
    assert body["total"] == 1


async def test_filter_counts_are_counted_among_donors(
    client: AsyncClient, token: str, four: dict[str, DonorModel]
) -> None:
    """Счётчики фильтров — по донорам экрана, а не по всей таблице: «подходит · 4»
    над списком из одного донора читалось бы как сбой."""
    body = (await client.get("/api/donors", headers=bearer(token))).json()

    assert body["counts"] == {"suitable": 1}
    assert body["countries"] == {"gb": 1}
    assert body["freshness"] == {"fresh": 1}


async def test_filter_does_not_reach_past_donors(
    client: AsyncClient, token: str, four: dict[str, DonorModel]
) -> None:
    """Фильтр сужает доноров, а не таблицу целиком: кандидат из США
    фильтром по США не находится."""
    body = (await client.get("/api/donors?geo=us", headers=bearer(token))).json()

    assert body["rows"] == []
    assert body["total"] == 0


async def test_export_of_the_found_carries_only_donors(
    client: AsyncClient, token: str, four: dict[str, DonorModel]
) -> None:
    response = await client.get("/api/donors/export", headers=bearer(token))

    assert [row["домен"] for row in _rows(response.content)] == ["accepted.example.test"]
    assert response.headers["x-export-rows"] == "1"
    assert response.headers["x-export-asked"] == "1"


async def test_picked_that_stopped_being_donors_do_not_reach_the_file(
    client: AsyncClient, token: str, four: dict[str, DonorModel]
) -> None:
    """Отметили четырёх, донор из них один: остальные в файл не идут, а ответ
    говорит, сколько их и почему — «уже не донор» и «записи нет» раздельно."""
    ids = [donor.id for donor in four.values()] + [2_000_000_000]

    response = await client.post("/api/donors/export", json={"ids": ids}, headers=bearer(token))

    assert response.status_code == 200, response.text
    assert [row["домен"] for row in _rows(response.content)] == ["accepted.example.test"]
    assert response.headers["x-export-rows"] == "1"
    assert response.headers["x-export-asked"] == "5"
    assert response.headers["x-export-not-donors"] == "3"
    assert response.headers["x-export-missing"] == "1"


async def test_the_card_opens_for_any_record_and_says_who_it_is(
    client: AsyncClient, token: str, four: dict[str, DonorModel], session: AsyncSession
) -> None:
    """Карточка открывается у любой записи и называет решение — и прогон,
    в очереди которого его принимают или приняли."""
    run_id = await session.scalar(
        select(RunCandidateModel.run_id).where(
            RunCandidateModel.domain_id == four["undecided"].domain_id
        )
    )
    cards = {
        key: (await client.get(f"/api/donors/{donor.id}", headers=bearer(token))).json()
        for key, donor in four.items()
    }

    assert {key: (card["review"], card["review_run"]) for key, card in cards.items()} == {
        "undecided": (None, run_id),
        "rejected": ("rejected", run_id),
        "accepted": ("accepted", run_id),
        "returned": (None, run_id),
    }


async def test_overview_is_a_funnel_through_the_same_rule(
    session: AsyncSession, four: dict[str, DonorModel]
) -> None:
    """Главная: проверено доменов — все записи; доноры — принятые; адрес —
    среди доноров, то есть та же длина, что у списка `?has_contact=true`."""
    donors = (await overview(session)).donors

    assert (donors.total, donors.suitable) == (4, 4)
    assert (donors.accepted, donors.rejected) == (1, 1)
    assert donors.with_email == 1


async def test_overview_address_tile_matches_the_list_it_leads_to(
    client: AsyncClient, token: str, session: AsyncSession, four: dict[str, DonorModel]
) -> None:
    listed = (await client.get("/api/donors?has_contact=true", headers=bearer(token))).json()

    assert (await overview(session)).donors.with_email == listed["total"]


async def test_waiting_names_the_path_to_the_first_donors(
    client: AsyncClient, token: str, four: dict[str, DonorModel], session: AsyncSession
) -> None:
    """Пустой экран доноров ведёт туда, где доноров принимают: сколько ждёт
    решения и в очереди какого прогона — тем же правилом, что у главной."""
    body = (await client.get("/api/donors", headers=bearer(token))).json()
    view = await overview(session)

    assert body["waiting"] == {"domains": view.waiting.review, "runs": view.waiting.review_runs}
    assert body["waiting"]["domains"] == 2
