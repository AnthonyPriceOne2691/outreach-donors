"""«Гео» — топ-5 стран донора в таблице, в фильтре и в файле (решение владельца 16.10.2026).

Оператор видел в колонке «Гео» одну страну («США · 21%»), а нужны все пять и фильтр
по ним: ТЗ называет «org_traffic по топ-5 странам» обязательным полем донора. Строка
списка несёт разбивку и признак неполной, как карточка; фильтр «Гео» — «страна в топ-5»
(верхняя или в разбивке), и его счётчики считаются тем же условием; выгрузка — колонкой
«страны (топ-5)» словами экрана.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Полная разбивка: США первыми, за ними Великобритания и Канада.
FULL = [
    {"country": "us", "org_traffic": 5500, "share": 0.55},
    {"country": "gb", "org_traffic": 1200, "share": 0.12},
    {"country": "ca", "org_traffic": 800, "share": 0.08},
]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    geo: str | None,
    breakdown: Any = None,
    partial: bool = False,
    dr: int = 30,
) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=dr,
        geo=geo,
        geo_breakdown=breakdown,
        geo_top_share=breakdown[0]["share"] if breakdown else None,
        geo_partial=partial,
        review="accepted",
    )
    session.add(donor)
    await session.flush()
    return donor


@pytest.fixture
async def donors(session: AsyncSession) -> None:
    """Полная разбивка, неполная (до 16.10.2026), заведённый руками и пустая разбивка."""
    await _donor(session, "full.example.test", geo="us", breakdown=FULL, dr=60)
    await _donor(
        session,
        "partial.example.test",
        geo="us",
        breakdown=[{"country": "us", "org_traffic": 8000, "share": 0.8}],
        partial=True,
        dr=50,
    )
    await _donor(session, "manual.example.test", geo="de", dr=40)
    # Пустая разбивка лежит JSON-значением null, а не пустотой столбца.
    await _donor(session, "empty.example.test", geo=None, breakdown=None, dr=20)
    await session.commit()


def _rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["host"]: row for row in body["rows"]}


class TestRowCarriesTheTopFive:
    async def test_breakdown_and_partial_mark_in_each_row(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        rows = _rows((await client.get("/api/donors", headers=bearer(token))).json())

        full = rows["full.example.test"]
        assert full["geo_breakdown"] == [
            {"country": "us", "share": 0.55},
            {"country": "gb", "share": 0.12},
            {"country": "ca", "share": 0.08},
        ]
        assert full["geo_partial"] is False
        partial = rows["partial.example.test"]
        assert partial["geo_breakdown"] == [{"country": "us", "share": 0.8}]
        assert partial["geo_partial"] is True
        assert rows["manual.example.test"]["geo_breakdown"] is None
        assert rows["empty.example.test"]["geo_breakdown"] is None

    async def test_unreadable_entries_are_skipped_not_fatal(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Строка разбивки без доли или без страны — пропускается, список не падает."""
        broken = [{"country": "us", "share": 0.5}, {"country": "gb"}, {"share": 0.1}, "fr"]
        await _donor(session, "odd.example.test", geo="us", breakdown=broken)
        await session.commit()

        response = await client.get("/api/donors", headers=bearer(token))

        assert response.status_code == 200, response.text
        row = _rows(response.json())["odd.example.test"]
        assert row["geo_breakdown"] == [{"country": "us", "share": 0.5}]


class TestCountryInTopFive:
    async def test_country_that_is_not_first_finds_the_donor(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        """Великобритания — вторая у одного донора: фильтр по ней его находит."""
        body = (await client.get("/api/donors?geo=GB", headers=bearer(token))).json()

        assert list(_rows(body)) == ["full.example.test"]

    async def test_top_country_still_finds_everyone_with_it(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        """Верхняя страна — и с полной разбивкой, и с неполной, и без разбивки вовсе."""
        us = (await client.get("/api/donors?geo=us", headers=bearer(token))).json()
        de = (await client.get("/api/donors?geo=de", headers=bearer(token))).json()

        assert list(_rows(us)) == ["full.example.test", "partial.example.test"]
        assert list(_rows(de)) == ["manual.example.test"]

    async def test_options_are_every_country_of_the_top_five_counted_once(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        """Пункты фильтра — и страны разбивок; донор — один раз на страну, хоть она
        у него и верхняя, и в разбивке."""
        body = (await client.get("/api/donors", headers=bearer(token))).json()

        assert body["countries"] == {"us": 2, "gb": 1, "ca": 1, "de": 1}

    async def test_each_count_is_what_its_filter_finds(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        """Счётчик пункта и фильтр — одно условие: «Канада · 1» находит одного."""
        countries = (await client.get("/api/donors", headers=bearer(token))).json()["countries"]

        found = {
            code: (await client.get(f"/api/donors?geo={code}", headers=bearer(token))).json()[
                "total"
            ]
            for code in countries
        }

        assert found == countries


def _file(body: bytes) -> dict[str, dict[str, str]]:
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";")
    header, *rows = list(reader)
    return {row[0]: dict(zip(header, row, strict=True)) for row in rows}


class TestFileColumn:
    async def test_top_five_in_words_of_the_screen(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        """«США 55%; Великобритания 12%; …» — доли тем же округлением, что «доля
        региона»; у неполной — пометка, что остальные не запрашивались."""
        rows = _file((await client.get("/api/donors/export", headers=bearer(token))).content)

        assert rows["full.example.test"]["страны (топ-5)"] == (
            "США 55%; Великобритания 12%; Канада 8%"
        )
        assert rows["full.example.test"]["доля региона"] == "55%"
        assert rows["partial.example.test"]["страны (топ-5)"] == (
            "США 80%; остальные не запрашивались"
        )
        assert rows["manual.example.test"]["страны (топ-5)"] == ""

    async def test_the_file_answers_the_same_filter(
        self, client: AsyncClient, token: str, donors: None
    ) -> None:
        rows = _file((await client.get("/api/donors/export?geo=ca", headers=bearer(token))).content)

        assert list(rows) == ["full.example.test"]
