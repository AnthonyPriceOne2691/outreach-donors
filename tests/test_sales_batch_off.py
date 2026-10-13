"""Пачки продаж нет, пока включена автоотправка (решение владельца 13.10).

Пачка («Отправить очередь») выбирала дневной лимит пяти ящиков продаж за секунды — мимо паузы
между первыми письмами ящика, которую держит проход автоотправки (замер стенда ревью стыков:
десять писем за 0,58 с, по 2–3 с ящика подряд). Пока автоотправка включена, экран «Очередь
писем» вместо кнопки говорит строкой, что письма уходят сами и как отправить вручную, а маршрут
пачки отказывает словами (409): прямой вызов API решения не обходит. Предохранитель почты продаж
не снят — проход не идёт, и пачка нужна: ею проверяют отправку на своих адресах. Пачка доноров
и рекламодателей — как была. Письмо по одному («Отправить» у письма) решение не трогает.

Очередь задач — подставная, тексты и адреса выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.sales import auto_lines
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_send import _Jobs, _queued

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SEND = "/api/letters/send-queue"
QUEUE = "/api/sales/queue"
#: Отказ пачки продаж, пока включена автоотправка, — целиком, как его увидит человек.
REFUSAL = (
    "Очередь писем не отправлена: пачкой письма продаж не уходят, пока включена автоотправка — "
    "письма уходят сами, по одному с ящика за проход и с паузой между письмами ящика; отправить "
    "очередь вручную — выключить автоотправку (SALES_AUTOSEND)"
)
#: Строка экрана вместо кнопки пачки.
LINE = (
    "Пачкой письма продаж не уходят, пока включена автоотправка: письма уходят сами, по одному с "
    "ящика за проход и с паузой между письмами ящика; отправить очередь вручную — выключить "
    "автоотправку (SALES_AUTOSEND)."
)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin@batch-off.example.test", role=UserRole.ADMIN)
    return bearer(await sign_in("admin@batch-off.example.test"))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Очередь задач пачки — подставная: видно, поставлена ли задача."""
    found = _Jobs()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: found)
    return found


@pytest.fixture
def screen_jobs(monkeypatch: pytest.MonkeyPatch) -> _AutoJobs:
    """Очередь задач экрана «Очередь писем» (строка автосборки) — подставная."""
    found = _AutoJobs()
    monkeypatch.setattr("backend.api.sales.queue.sales_queue", lambda: found)
    return found


async def _batch_off(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> object:
    response = await client.get(f"{QUEUE}?hypothesis={hypothesis_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["batch_off"]


async def test_the_sales_batch_is_refused_in_words_while_autosend_is_on(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Автоотправка включена, предохранитель снят — пачка продаж отказывает словами до счёта и
    до задачи: прямой вызов маршрута решения не обходит. Выключили — пачка как была."""
    await _queued(session, world)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)

    refused = await client.post(SEND, json={"stage": "sales"}, headers=headers)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", False)
    taken = await client.post(SEND, json={"stage": "sales"}, headers=headers)

    assert refused.status_code == 409
    assert refused.json()["detail"] == REFUSAL
    assert taken.status_code == 200, taken.text
    assert [args[1] for args in jobs.enqueued] == ["sales"]


async def test_a_set_allowlist_keeps_the_batch_for_checks_on_own_addresses(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    screen_jobs: _AutoJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тумблер включён, а список разрешённых адресатов не пуст — проход не идёт
    (`autosend.guard`), и пачка нужна: ею проверяют отправку на своих адресах. Кнопка есть,
    маршрут ставит задачу."""
    await _queued(session, world)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "me@ours.example.test")

    response = await client.post(SEND, json={"stage": "sales"}, headers=headers)
    line = await _batch_off(client, headers, world.hypothesis_id)

    assert response.status_code == 200, response.text
    assert [args[1] for args in jobs.enqueued] == ["sales"]
    assert line is None


async def test_the_donors_batch_does_not_ask_about_sales_autosend(
    client: AsyncClient, headers: dict[str, str], jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пачка доноров — как была: включённая автоотправка продаж её не касается — отказ пустой
    очереди доноров своими словами, а не словами продаж."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")

    response = await client.post(SEND, json={"stage": "donors"}, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "В очереди этого этапа писем нет — отправлять нечего"
    assert jobs.enqueued == []


async def test_the_queue_screen_puts_a_line_instead_of_the_batch_button_while_autosend_is_on(
    client: AsyncClient,
    headers: dict[str, str],
    world: w.World,
    screen_jobs: _AutoJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Экран «Очередь писем» получает строку вместо кнопки пачки, пока автоотправка включена, —
    словами того же правила, что у отказа маршрута; выключена — строки нет, кнопка на месте."""
    off = await _batch_off(client, headers, world.hypothesis_id)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    on = await _batch_off(client, headers, world.hypothesis_id)

    assert (off, on) == (None, LINE)
    assert auto_lines.batch_line() == LINE
