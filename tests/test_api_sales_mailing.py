"""Вкладка «Рассылка» продаж через API (`GET /api/sales/mailing`): режим и адреса частями.

Форма ответа — поля, которые читает экран; строка на каждую гипотезу, и без лидов — с нулями; итог —
сумма строк, а у каждой строки — сумма частей; письма в полёте — внутри «ушло» (`in_flight`), не
своей частью. Строки автоотправки и автосборки — те же слова, что у
«Очереди писем». «Без письма» — одно число на трёх экранах: плитка «Очереди писем», «готовых лидов без
письма» недельного отчёта и части вкладки; «сборка напишет» — то же число у очереди и вкладки. Право —
«Продажи»: без него 403 словами. Адреса и тексты выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import MessageStatus, Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import hypotheses
from backend.features.sales.models import LeadStatus
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_mailing import (
    Book,
    _busy,
    _held,
    _no_name,
    _ready,
    _rejected,
    _stoplist,
    _with_first,
)

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
MAILING = "/api/sales/mailing"
QUEUE = "/api/sales/queue"
WEEKLY = "/api/sales/weekly"
NO_RIGHT = no_right(Permission.SALES)
#: Поля частей — их читает экран вкладки.
PARTS = {
    "total",
    "sent",
    "in_flight",
    "queued",
    "will_write",
    "wont_write",
    "not_ready",
    "removed",
    "wont_write_why",
    "removed_why",
}
NUMBERS = ("sent", "queued", "will_write", "wont_write", "not_ready", "removed")


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture(autouse=True)
def jobs(monkeypatch: pytest.MonkeyPatch) -> _AutoJobs:
    """Очередь долгих задач продаж — подставная у обеих ручек: строка автосборки читает её."""
    found = _AutoJobs()
    monkeypatch.setattr("backend.api.sales.mailing.sales_bulk_queue", lambda: found)
    monkeypatch.setattr("backend.api.sales.queue.sales_bulk_queue", lambda: found)
    return found


async def _mixed(session: AsyncSession, hypothesis_id: int) -> None:
    """Лиды гипотезы во всех частях: сборка напишет, ждёт (адрес, стоп-лист, сайт в работе, нет
    имени), письмо в очереди, ушло, не готов, снят."""
    book = Book(session, hypothesis_id)
    for make in (
        _ready,
        _ready,
        _held,
        _stoplist,
        _busy,
        _no_name,
        _with_first(MessageStatus.QUEUED),
        _with_first(MessageStatus.DELIVERED),
        _rejected("duplicate"),
    ):
        await make(book)
    await book.lead(status=LeadStatus.NEW)
    await session.commit()


async def _get(client: AsyncClient, path: str, headers: dict[str, str]) -> Any:
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _added(parts: dict[str, Any]) -> int:
    return sum(int(parts[name]) for name in NUMBERS)


async def test_the_answer_has_every_part_and_rows_add_up_with_empty_hypotheses_as_zeros(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await _mixed(session, world.hypothesis_id)
    empty = (await hypotheses.add(session, "Пустая выдуманная гипотеза", None)).id
    await session.commit()

    body = await _get(client, MAILING, headers)

    assert set(body) == {"autosend", "autobuild", "rows", "total"}
    assert set(body["autosend"]) == set(body["autobuild"]) == {"enabled", "line"}
    assert [row["hypothesis_id"] for row in body["rows"]] == [world.hypothesis_id, empty]
    ours, nothing = body["rows"]
    assert set(ours) == {"hypothesis_id", "name", "parts"}
    assert set(ours["parts"]) == PARTS
    assert {why["words"] for why in ours["parts"]["wont_write_why"]} >= {"стоп-лист"}
    assert all(set(why) == {"words", "count"} for why in ours["parts"]["removed_why"])
    assert [ours["parts"][name] for name in ("total", *NUMBERS)] == [10, 1, 1, 2, 4, 1, 1]
    assert nothing == {
        "hypothesis_id": empty,
        "name": "Пустая выдуманная гипотеза",
        "parts": dict.fromkeys(PARTS - {"wont_write_why", "removed_why"}, 0)
        | {"wont_write_why": [], "removed_why": []},
    }
    for parts in (ours["parts"], body["total"]):
        assert parts["total"] == _added(parts)
    assert body["total"] == ours["parts"]


async def test_letters_in_flight_are_inside_sent_and_counted_apart(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Первое письмо «отправляется»: лид — в «ушло», и сколько таких — `in_flight` у строки и итога;
    «всего» — по-прежнему сумма частей, а «в полёте» в неё не входит отдельно."""
    book = Book(session, world.hypothesis_id)
    for make in (_with_first(MessageStatus.SENDING), _with_first(MessageStatus.DELIVERED), _ready):
        await make(book)
    await session.commit()

    body = await _get(client, MAILING, headers)

    [ours] = [row["parts"] for row in body["rows"] if row["hypothesis_id"] == world.hypothesis_id]
    assert (ours["total"], ours["sent"], ours["in_flight"], ours["will_write"]) == (3, 2, 1, 1)
    assert ours["total"] == _added(ours)
    assert body["total"] == ours


async def test_the_mode_lines_are_the_lines_of_the_letter_queue(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Автоотправка и автосборка включены: строки со словами прохода — те же, что у очереди."""
    await _mixed(session, world.hypothesis_id)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)

    mailing = await _get(client, MAILING, headers)
    queue = await _get(client, f"{QUEUE}?hypothesis={world.hypothesis_id}", headers)

    assert (mailing["autosend"]["enabled"], mailing["autobuild"]["enabled"]) == (True, True)
    assert "мощность ящиков продаж — 20 писем" in mailing["autobuild"]["line"]
    assert (mailing["autosend"], mailing["autobuild"]) == (queue["autosend"], queue["autobuild"])


async def test_without_letters_is_one_number_on_the_queue_the_week_and_the_tab(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Плитка «Лидов без письма» — все «готов» без письма (и те, что ждут сайт в работе у другого
    направления), а не только тех, кому сборка напишет; недельный отчёт — то же; вкладка делит их
    на «сборка напишет» и «не напишет»."""
    await _mixed(session, world.hypothesis_id)

    mailing = await _get(client, MAILING, headers)
    queue = await _get(client, f"{QUEUE}?hypothesis={world.hypothesis_id}", headers)
    week = await _get(client, WEEKLY, headers)

    [tab] = [row["parts"] for row in mailing["rows"] if row["hypothesis_id"] == world.hypothesis_id]
    [report] = [
        row["numbers"] for row in week["rows"] if row["hypothesis_id"] == world.hypothesis_id
    ]
    unwritten = tab["will_write"] + tab["wont_write"]
    assert (queue["unwritten"], report["unwritten"], unwritten) == (6, 6, 6)
    assert queue["will_write"] == tab["will_write"] == 2
    assert queue["queued"] == report["letters_queued"] == tab["queued"] == 1


async def test_without_the_sales_right_the_tab_refuses_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(MAILING, headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
