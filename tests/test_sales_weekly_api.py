"""Недельный отчёт продаж через API — экран и выгрузка CSV (срез 6.3, T3; A3 — «на экране»).

Экран отдаёт тот же отчёт, что уходит в группу продаж: разделы — теми же словами
(`weekly_text.sections` сборки за ту же неделю), числа по гипотезам — те же; выгрузка — те же
числа строками таблицы. Как отчёт ушёл в группу — тоже на экране: ушёл, не доставлен с причиной
и сроком повтора, не уходит. Права — `sales`; отказы словами. Часы маршрута — выдуманные
(`test_sales_weekly_rows.BUILT`), строки базы — те же, что у сборки (`test_sales_weekly.py`).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from pathlib import Path

import pytest
from backend.api.sales import weekly as weekly_routes
from backend.api.sales.weekly import (
    NOT_MONDAY,
    NOT_OVER,
    SalesWeeklyView,
    WeeklyDelivery,
    WeeklyNumbers,
    WeeklyRow,
    WeeklySection,
)
from backend.config import sales as cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import weekly, weekly_text
from backend.features.sales.weekly_models import SalesWeeklyReportModel, WeeklyStatus
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_weekly import week_world
from tests.test_sales_weekly_rows import BUILT, WEEK, WeekRows, msk

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
WEEKLY = "/api/sales/weekly"
FILE = "/api/sales/weekly.csv"
NO_RIGHT = no_right(Permission.SALES)
TYPES = (Path(__file__).resolve().parent.parent / "frontend/src/api/salesWeekly.ts").read_text(
    encoding="utf-8"
)


@pytest.fixture(autouse=True)
def frozen(api_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Часы маршрута — понедельник после выдуманной недели; отчёт уходит в 09:00."""
    api_app.dependency_overrides[weekly_routes.clock] = lambda: BUILT
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", (0, time(9, 0)))


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def test_screen_shows_the_report_that_goes_to_the_group(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    await week_world(WeekRows(session))
    await session.commit()

    response = await client.get(WEEKLY, params={"week": "2027-03-01"}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    report = await weekly.build(session, WEEK, now=BUILT)
    assert body["sections"] == [
        {"key": part.key, "title": part.title, "lines": part.lines}
        for part in weekly_text.sections(report)
    ]
    assert body["total"] == WeeklyNumbers.of(report.total).model_dump()
    assert [(row["name"], row["numbers"]) for row in body["rows"]] == [
        (row.name, WeeklyNumbers.of(row.numbers).model_dump()) for row in report.rows
    ]
    assert (body["week"], body["last_day"], body["weeks"][:2], len(body["weeks"])) == (
        "2027-03-01",
        "2027-03-07",
        ["2027-03-01", "2027-02-22"],
        weekly.WEEKS_SHOWN,
    )
    assert body["total"]["sent"] == 7, "сверка не пустая"


async def test_without_a_week_the_screen_shows_the_last_one(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(WEEKLY, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["week"] == "2027-03-01"


async def test_csv_carries_the_numbers_of_the_screen(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    # CSV — те же числа, что на экране
    await week_world(WeekRows(session))
    await session.commit()

    shown = (await client.get(WEEKLY, params={"week": "2027-03-01"}, headers=headers)).json()
    response = await client.get(FILE, params={"week": "2027-03-01"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="sales-week-2027-03-01.csv"'
    )
    table = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    found = {(part, of, what): value for part, of, what, value in table[1:]}
    for row in [*shown["rows"], {"name": "Всего", "numbers": shown["total"]}]:
        numbers, name = row["numbers"], row["name"]
        assert found[("контакты", name, "загружено за неделю")] == str(numbers["loaded"])
        assert found[("отправки", name, "первое письмо получили лидов")] == str(numbers["sent"])
        assert found[("отправки", name, "писем, 2-й шаг")] == str(numbers["letters"][1])
        assert found[("доставляемость", name, "отказ (лиды недели)")] == str(numbers["bounced"])
        assert found[("ответы", name, "ответов за неделю")] == str(numbers["answers"])
        assert found[("ответы", name, "вид не разобран")] == str(numbers["replies"]["unparsed"])
        assert found[("диалоги", name, "молчащих")] == str(numbers["silent"])
        assert found[("лиды поимённо", name, "передано за неделю")] == str(numbers["handed"])
        assert found[("план", name, "готовых лидов без письма")] == str(numbers["unwritten"])


# --- как отчёт ушёл в группу ------------------------------------------------------------------------


async def _delivery(client: AsyncClient, headers: dict[str, str]) -> dict[str, object]:
    response = await client.get(WEEKLY, params={"week": "2027-03-01"}, headers=headers)
    assert response.status_code == 200, response.text
    delivery: dict[str, object] = response.json()["delivery"]
    return delivery


async def test_screen_says_when_the_report_goes(
    client: AsyncClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await _delivery(client, headers) == {
        "status": "none",
        "sent_at": None,
        "due_at": None,
        "error": None,
        "schedule": "в понедельник в 09:00 по Москве, за прошлую неделю",
    }
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", None)
    assert (await _delivery(client, headers))["status"] == "off"


async def test_a3_undelivered_report_is_on_the_screen_with_why_and_when(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    # A3: Telegram недоступен — отчёт доступен на экране
    session.add(
        SalesWeeklyReportModel(
            week_start=WEEK.start,
            status=WeeklyStatus.UNDELIVERED.value,
            text="…",
            error="Telegram отказал (HTTP 503: Unavailable) — повтор позже",
            tries=2,
            due_at=msk(8, 9, 20),
        )
    )
    await session.commit()

    delivery = await _delivery(client, headers)

    assert (delivery["status"], delivery["error"]) == (
        "undelivered",
        "Telegram отказал (HTTP 503: Unavailable) — повтор позже",
    )
    assert datetime.fromisoformat(str(delivery["due_at"])) == msk(8, 9, 20)


async def test_sent_report_says_when(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    session.add(
        SalesWeeklyReportModel(
            week_start=WEEK.start,
            status=WeeklyStatus.SENT.value,
            text="…",
            tries=0,
            sent_at=msk(8, 9, 1),
        )
    )
    await session.commit()

    delivery = await _delivery(client, headers)

    assert delivery["status"] == "sent"
    assert datetime.fromisoformat(str(delivery["sent_at"])) == msk(8, 9, 1)


# --- отказы -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [WEEKLY, FILE])
@pytest.mark.parametrize(
    ("week", "said"),
    [("2027-03-02", NOT_MONDAY), ("2027-03-08", NOT_OVER), ("2027-03-15", NOT_OVER)],
)
async def test_week_that_is_not_a_monday_or_not_over_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str], path: str, week: str, said: str
) -> None:
    response = await client.get(path, params={"week": week}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (422, said)


@pytest.mark.parametrize("path", [WEEKLY, FILE])
async def test_without_the_sales_right_the_report_refuses_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, path: str
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(path, headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)


async def test_weekly_routes_are_these(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(WEEKLY)
        for method in methods
    }

    assert in_app == {("GET", WEEKLY), ("GET", FILE)}


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст."""
    found = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", TYPES, re.DOTALL)
    assert found is not None, f"в salesWeekly.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_screen_reads_the_report_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки."""
    for model in (SalesWeeklyView, WeeklyRow, WeeklySection, WeeklyDelivery, WeeklyNumbers):
        assert _screen_fields(model.__name__) == set(model.model_fields), model.__name__
