"""Выгрузка доноров: в файле те же значения, что в строке таблицы.

До 25.09.2026 файл выходил с доменами и пустыми остальными колонками:
значения брались у строки таблицы, а вердикт, причина, DR и трафик лежат
у донора. Тесты проверяли заголовок и то, что домены на месте, — и были
зелёными. С экрана выгрузка не скачивалась вовсе (ссылка без пропуска
получала 401), поэтому пустого файла никто и не видел. Нашёл его живой
прогон: первое же скачивание с пропуском.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.donors import export
from backend.features.donors.browse import DonorBrowser, DonorFilters
from backend.features.donors.wording import CONTACT_STATUS_TITLES, DONOR_STATUS_TITLES
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

CHECKED = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def donor(session: AsyncSession) -> DonorModel:
    """Донор, у которого заполнена каждая колонка файла."""
    domain = DomainModel(host="weak.example.test")
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.UNSUITABLE,
        reject_reason="dr ниже порога",
        dr=8,
        org_traffic=2_400_000_000,
        geo="de",
        geo_top_share=0.4,
        contact_status=ContactStatus.FOUND,
        last_price=Decimal("50.00"),
        last_price_currency="EUR",
        metrics_refreshed_at=CHECKED,
        # Выгружаются доноры — принятые человеком (решение 26.09.2026).
        review="accepted",
    )
    session.add(donor)
    session.add(
        ContactModel(domain_id=domain.id, email="ads@weak.example.test", source=ContactSource.PAGE)
    )
    await session.commit()
    return donor


def _rows(body: bytes) -> list[dict[str, str]]:
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")), delimiter=";")
    header, *rows = list(reader)
    return [dict(zip(header, row, strict=True)) for row in rows]


async def test_every_column_carries_the_value(
    client: AsyncClient, token: str, donor: DonorModel
) -> None:
    response = await client.get(
        "/api/donors/export", params={"status": "unsuitable"}, headers=bearer(token)
    )

    assert response.status_code == 200, response.text
    assert _rows(response.content) == [
        {
            "домен": "weak.example.test",
            "вердикт": "не подходит",
            "причина отсева": "dr ниже порога",
            "DR": "8",
            "трафик": "2400000000",
            "регион": "Германия",
            "доля региона": "40%",
            "адресов": "1",
            "исход поиска": "адрес найден",
            "цена": "50.00",
            "валюта": "EUR",
            "метрики от": "2026-09-18",
        }
    ]


async def test_file_is_the_same_rows_as_the_screen_page(
    client: AsyncClient, token: str, donor: DonorModel
) -> None:
    """Колонки те же, что в строке таблицы: одно и то же значение, а не два
    разных чтения одного донора."""
    screen = (await client.get("/api/donors", headers=bearer(token))).json()["rows"][0]
    exported = _rows((await client.get("/api/donors/export", headers=bearer(token))).content)[0]

    assert exported["домен"] == screen["host"]
    # Коды экран переводит сам (`labels.ts`) — теми же словами, что файл:
    # их сверку держит `test_donor_wording.py`.
    assert exported["вердикт"] == DONOR_STATUS_TITLES[screen["status"]]
    assert exported["причина отсева"] == screen["reject_reason"]
    assert int(exported["DR"]) == screen["dr"]
    assert int(exported["трафик"]) == screen["org_traffic"]
    assert int(exported["адресов"]) == screen["contacts"]
    assert exported["исход поиска"] == CONTACT_STATUS_TITLES[screen["contact_status"]]


async def test_unknown_column_is_loud_not_empty(
    session: AsyncSession, donor: DonorModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Поле, которого нет ни у строки, ни у донора, — ошибка, а не пустая
    ячейка: пустая колонка и есть то, что прятало дефект."""
    page = await DonorBrowser(session).page(DonorFilters())
    monkeypatch.setattr(export, "COLUMNS", (("опечатка", "no_such_field"),))

    with pytest.raises(AttributeError):
        export.to_csv(page.rows)


async def test_codes_leave_the_file_as_words(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Регион, доля и исход поиска — словами и процентом, как на экране.

    До 25.09.2026 в файле стояли `us`, `0.19888…` и пустой исход у тех,
    кому адрес не искали, — а экран называет их «США · 20%» и «не искали».
    Причина отсева с кодом страны — названием, и у старой записи тоже.
    """
    domain = DomainModel(host="far.example.test")
    session.add(domain)
    await session.flush()
    session.add(
        DonorModel(
            domain_id=domain.id,
            status=DonorStatus.UNSUITABLE,
            reject_reason="ng не входит в топ-5 и даёт меньше 20%",
            geo="us",
            geo_top_share=0.19888,
            contact_status=None,
            review="accepted",
        )
    )
    await session.commit()

    exported = _rows((await client.get("/api/donors/export", headers=bearer(token))).content)
    row = next(one for one in exported if one["домен"] == "far.example.test")

    assert row["регион"] == "США"
    assert row["доля региона"] == "20%"
    assert row["исход поиска"] == "не искали"
    assert row["причина отсева"] == "Нигерия не входит в топ-5 и даёт меньше 20%"
