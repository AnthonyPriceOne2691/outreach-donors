"""Строки «Очереди писем» об автоотправке и автосборке — через `GET /api/sales/queue`.

Строку говорит сервер тем же снимком, каким судит проход (`sales/auto_lines.py`): включено ли,
как часто, сколько писем в окне получателя, почему стоит; хватает ли очереди на сутки и что
собрано само. Часы — настоящие (маршрут их не принимает): окно мира открыто всегда. Очередь
задач — подставная. Тексты и адреса выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus
from backend.features.core.models.access import UserModel
from backend.features.letters.sending import Sending
from backend.features.sales import autobuild, autosend
from backend.features.sales.auto_lines import BUILD_OFF, SEND_OFF, pace
from backend.features.sales.queue_start import build_job_id
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_autosend import JANE, OLGA, _letters
from tests.test_sales_send import _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

QUEUE = "/api/sales/queue"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("seller@ours.example.test", permissions={"send": True})
    return bearer(await sign_in("seller@ours.example.test"))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _AutoJobs:
    found = _AutoJobs()
    monkeypatch.setattr("backend.api.sales.queue.sales_queue", lambda: found)
    return found


async def _lines(
    client: AsyncClient, headers: dict[str, str], hypothesis_id: int
) -> dict[str, dict[str, object]]:
    response = await client.get(f"{QUEUE}?hypothesis={hypothesis_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    return {"autosend": body["autosend"], "autobuild": body["autobuild"]}


async def test_switched_off_both_lines_say_so_with_the_setting(
    client: AsyncClient, headers: dict[str, str], world: w.World
) -> None:
    lines = await _lines(client, headers, world.hypothesis_id)

    assert lines == {
        "autosend": {"enabled": False, "line": SEND_OFF},
        "autobuild": {"enabled": False, "line": BUILD_OFF},
    }
    assert "включается настройкой SALES_AUTOSEND" in SEND_OFF


async def test_unknown_outcome_is_said_on_the_screen_with_the_letters_in_the_window(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letters = await _letters(session, world, JANE, OLGA)
    letters[0].status = MessageStatus.SENDING
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)

    lines = await _lines(client, headers, world.hypothesis_id)

    line = str(lines["autosend"]["line"])
    assert lines["autosend"]["enabled"] is True
    assert line.startswith(f"Автоотправка включена: {pace()}. В окне получателя сейчас 1 письмо.")
    assert f"Стоит: исход письма №{letters[0].id} неизвестен" in line


async def test_going_and_then_waiting_for_the_box_pause(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letters = await _letters(session, world, JANE, OLGA)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)

    going = await _lines(client, headers, world.hypothesis_id)
    now = datetime.now(UTC)
    await Sending(session, _transports(), now=now).send(letters[0].id)
    resting = await _lines(client, headers, world.hypothesis_id)

    assert str(going["autosend"]["line"]).endswith(
        "В окне получателя сейчас 2 письма. Письма уходят"
    )
    until = now + autosend.pause_of(world.sales_box.id, letters[0].id)
    assert str(resting["autosend"]["line"]).endswith(
        f"В окне получателя сейчас 1 письмо. Ящики на паузе — ближайший освободится в "
        f"{until:%H:%M} UTC"
    )


async def test_autobuild_line_says_the_shortage_whom_it_builds_and_what_it_built(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    world: w.World,
    jobs: _AutoJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await w.lead(session, world.hypothesis_id, JANE)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)

    before = await _lines(client, headers, world.hypothesis_id)
    await autobuild.run(session, jobs, now=datetime.now(UTC))
    running = await _lines(client, headers, world.hypothesis_id)
    jobs.known[build_job_id(world.hypothesis_id)].finish(
        {"prepared": 3, "refreshed": 0, "stopped": None}
    )
    done = await _lines(client, headers, world.hypothesis_id)

    head = (
        "Автосборка включена: очередь — на сутки вперёд, дневные капы ящиков продаж с разгоном — "
        "20 писем, в очереди 0 писем"
    )
    assert before["autobuild"] == {
        "enabled": True,
        "line": f"{head}; не хватает 20 писем — ближайший проход соберёт гипотезу "
        "«Выдуманная гипотеза», до 20 писем",
    }
    assert str(running["autobuild"]["line"]).startswith(
        f"{head}; не хватает 20 писем, но не собирается: идёт сборка гипотезы «Выдуманная гипотеза»"
    )
    assert str(running["autobuild"]["line"]).endswith("«Выдуманная гипотеза», до 20 писем: идёт")
    assert str(done["autobuild"]["line"]).endswith(
        "«Выдуманная гипотеза», до 20 писем: собрано 3 письма"
    )
