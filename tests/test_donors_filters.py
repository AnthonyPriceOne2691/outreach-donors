"""Фильтры под каждой колонкой доноров: трафик, гео, данные — на сервере.

Замечание 26.09.2026: «добить все фильтры (сделать и для других колонок)».
Фильтр сужает на сервере, а не пришедшую страницу, и одним набором
параметров у таблицы и у выгрузки: файл отвечает на тот же вопрос, что экран.

Отдельно — срок годности метрик: значок «в сроке» в строке и фильтр
«Данные» считаются одним условием, и на самой границе срока они обязаны
сказать одно и то же.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.config import filters as filters_cfg
from backend.features.core.domain import DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors import export
from backend.features.donors.browse import DonorBrowser, DonorFilters, Freshness
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Сейчас, а не дата: маршрут меряет срок от настоящих часов, и зашитая дата
#: через девяносто дней превратила бы «в сроке» в «пора обновить».
NOW = datetime.now(UTC)
TTL = timedelta(days=filters_cfg.METRICS_TTL_DAYS)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    traffic: int | None = None,
    geo: str | None = None,
    refreshed: datetime | None = None,
    dr: int = 30,
) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=dr,
        org_traffic=traffic,
        geo=geo,
        metrics_refreshed_at=refreshed,
        review="accepted",
    )
    session.add(donor)
    await session.flush()
    return donor


@pytest.fixture
async def spread(session: AsyncSession) -> None:
    """Доноры разного трафика, стран и свежести метрик."""
    await _donor(
        session, "huge.example.test", traffic=5_190_644_809, geo="us", refreshed=NOW, dr=90
    )
    await _donor(
        session, "big.example.test", traffic=2_400_000, geo="us", refreshed=NOW - TTL * 2, dr=60
    )
    await _donor(session, "small.example.test", traffic=900, geo="de", refreshed=NOW, dr=40)
    await _donor(session, "blank.example.test", dr=10)
    await session.commit()


def _hosts(body: dict[str, object]) -> list[str]:
    rows = body["rows"]
    assert isinstance(rows, list)
    return [row["host"] for row in rows]


class TestTraffic:
    async def test_not_lower_than_counts_billions(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        """Трафик бывает за пять миллиардов — порог в миллиард его не теряет."""
        body = (
            await client.get("/api/donors?min_traffic=1000000000", headers=bearer(token))
        ).json()

        assert _hosts(body) == ["huge.example.test"]

    async def test_threshold_is_inclusive_and_skips_the_unknown(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        """«Не ниже» — включительно; трафика нет — порог не пройден."""
        body = (await client.get("/api/donors?min_traffic=900", headers=bearer(token))).json()

        assert _hosts(body) == ["huge.example.test", "big.example.test", "small.example.test"]

    async def test_nonsense_is_refused(self, client: AsyncClient, token: str) -> None:
        response = await client.get("/api/donors?min_traffic=-1", headers=bearer(token))

        assert response.status_code == 422


class TestGeo:
    async def test_country_by_code_in_any_case(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        body = (await client.get("/api/donors?geo=US", headers=bearer(token))).json()

        assert _hosts(body) == ["huge.example.test", "big.example.test"]

    async def test_countries_come_with_counts(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        """Варианты фильтра — страны, какие есть у доноров, со счётчиками;
        донор без страны в список стран не попадает."""
        body = (await client.get("/api/donors", headers=bearer(token))).json()

        assert body["countries"] == {"us": 2, "de": 1}

    async def test_not_a_country_code_is_refused(self, client: AsyncClient, token: str) -> None:
        response = await client.get("/api/donors?geo=usa", headers=bearer(token))

        assert response.status_code == 422


class TestFreshness:
    async def test_each_state_filters_its_own(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        found = {
            state.value: _hosts(
                (
                    await client.get(f"/api/donors?freshness={state.value}", headers=bearer(token))
                ).json()
            )
            for state in Freshness
        }

        assert found == {
            "fresh": ["huge.example.test", "small.example.test"],
            "stale": ["big.example.test"],
            "never": ["blank.example.test"],
        }

    async def test_counts_by_state(self, client: AsyncClient, token: str, spread: None) -> None:
        body = (await client.get("/api/donors", headers=bearer(token))).json()

        assert body["freshness"] == {"fresh": 2, "stale": 1, "never": 1}

    async def test_badge_and_filter_agree_on_the_border(self, session: AsyncSession) -> None:
        """Одно условие на значок и на фильтр: на границе срока и в секунде
        от неё строка стоит под тем фильтром, чьё слово на её значке."""
        for index, shift in enumerate((-1, 0, 1)):
            await _donor(
                session,
                f"border-{index}.example.test",
                refreshed=NOW - TTL + timedelta(seconds=shift),
            )
        await _donor(session, "never.example.test")
        browser = DonorBrowser(session)

        shown = {
            row.host: row.freshness for row in (await browser.page(DonorFilters(), now=NOW)).rows
        }
        filtered = {
            row.host: state
            for state in Freshness
            for row in (await browser.page(DonorFilters(freshness=state), now=NOW)).rows
        }

        assert shown == filtered
        assert shown["border-1.example.test"] is Freshness.STALE  # ровно на границе — уже нет
        assert shown["border-2.example.test"] is Freshness.FRESH

    async def test_fresh_badge_is_the_same_word_as_before(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        """Прежнее поле `fresh` — то же правило, что новое `freshness`."""
        rows = (await client.get("/api/donors", headers=bearer(token))).json()["rows"]

        assert all(row["fresh"] is (row["freshness"] == "fresh") for row in rows)


def _rows(body: bytes) -> list[dict[str, str]]:
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";")
    header, *rows = list(reader)
    return [dict(zip(header, row, strict=True)) for row in rows]


class TestExportAsksTheSameQuestion:
    async def test_found_file_is_the_filtered_table(
        self, client: AsyncClient, token: str, spread: None
    ) -> None:
        query = "min_traffic=1000&geo=us&freshness=stale"
        table = (await client.get(f"/api/donors?{query}", headers=bearer(token))).json()
        response = await client.get(f"/api/donors/export?{query}", headers=bearer(token))

        assert [row["домен"] for row in _rows(response.content)] == _hosts(table)
        assert response.headers["x-export-rows"] == response.headers["x-export-asked"] == "1"

    async def test_file_says_when_it_holds_less_than_found(
        self,
        client: AsyncClient,
        token: str,
        spread: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Найдено больше потолка — в файле потолок, а заголовки говорят обе
        цифры: экран не обещает больше, чем в файле."""
        monkeypatch.setattr("backend.api.donors.routes.EXPORT_LIMIT", 2)

        response = await client.get("/api/donors/export", headers=bearer(token))

        assert len(_rows(response.content)) == 2
        assert (response.headers["x-export-rows"], response.headers["x-export-asked"]) == ("2", "4")

    async def test_list_names_the_ceiling(self, client: AsyncClient, token: str) -> None:
        body = (await client.get("/api/donors", headers=bearer(token))).json()

        assert body["export_limit"] == export.EXPORT_LIMIT


class TestPickedCeiling:
    async def test_nothing_picked_is_refused_with_words(
        self, client: AsyncClient, token: str
    ) -> None:
        response = await client.post("/api/donors/export", json={"ids": []}, headers=bearer(token))

        assert response.status_code == 400
        assert response.json()["detail"] == "Не отмечено ни одного донора — выгружать нечего."

    async def test_more_than_the_ceiling_is_refused_with_words(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(export, "EXPORT_LIMIT", 3)

        response = await client.post(
            "/api/donors/export", json={"ids": [1, 2, 3, 4]}, headers=bearer(token)
        )

        assert response.status_code == 400
        assert response.json()["detail"].startswith("Отмечено 4 — в файл за раз идёт не больше 3.")

    async def test_the_same_number_twice_is_one_pick(
        self, client: AsyncClient, token: str, spread: None, session: AsyncSession
    ) -> None:
        page = await DonorBrowser(session).page(DonorFilters())
        first = page.rows[0].donor.id

        response = await client.post(
            "/api/donors/export", json={"ids": [first, first]}, headers=bearer(token)
        )

        assert len(_rows(response.content)) == 1
        assert response.headers["x-export-asked"] == "1"
