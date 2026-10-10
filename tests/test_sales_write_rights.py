"""Запись текстов продаж — с правами `sales` и `send`, как в ядре; смотреть — с одним `sales`.

Цепочку писем, записи базы знаний, отправителя и сборку очереди меняет только тот, кому можно
отправлять, — как письмо ядра (`api/letters/routes.py`): текст уходит адресату, база знаний —
опора судьи, и обычная учётка не должна превращаться в рассылку. Чтение и предпросмотр ничего
не пишут и остаются под `sales`.

Каждый маршрут записи — тремя учётками: с одним `sales` — 403 словами о `send`, с одним `send` —
403 словами о `sales`, и в обоих отказах журнал пуст; с обоими правами запись проходит и ложится
в журнал с автором. Таблица сверяется с приложением: новый маршрут четырёх вкладок без строки
здесь — красный тест. Тексты, подписи и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import kb
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_chain_api import FIRST
from tests.test_sales_queue_api import jobs, world

__all__ = ["jobs", "world"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
NO_SALES = "Действие «sales» недоступно этой учётке"
NO_SEND = "Действие «send» недоступно этой учётке"

#: Учётки по правам. Оператору `sales` положено ролью, `send` выдают поимённо.
ONLY_SALES: dict[str, bool] | None = None
ONLY_SEND = {"sales": False, "send": True}
BOTH = {"send": True}

CHAIN = "/api/sales/chain"
KB = "/api/sales/kb"
SENDER = "/api/sales/sender"
QUEUE = "/api/sales/queue"
#: Пути четырёх вкладок: всё, что приложение отдаёт под ними, стоит в таблицах ниже.
TABS = (CHAIN, KB, SENDER, QUEUE)

PRICE = {
    "kind": "price_policy",
    "language": "ru",
    "title": "Цена аудита",
    "text": "Цену называем после короткого созвона.",
}
BRIEF = {"kind": "brief", "language": "ru", "title": "Кто мы", "text": "Студия примеров."}


@dataclass(frozen=True, slots=True)
class Ground:
    """Мир, где проходит каждый маршрут: продажи подключены, есть гипотеза и запись базы."""

    hypothesis_id: int
    entry_id: int
    jobs: Any


@dataclass(frozen=True, slots=True)
class Route:
    """Маршрут вкладки: метод, путь приложения (с адресом запроса), тело и след в журнале."""

    method: str
    #: Путь как в приложении; `{entry_id}` и `{hypothesis}` подставляет мир.
    path: str
    body: Callable[[Ground], dict[str, Any] | None] = lambda _: None
    ok: int = 200
    #: Что запись кладёт в журнал от имени автора; чтение не кладёт ничего.
    journal: AuditAction | None = None

    @property
    def template(self) -> str:
        return self.path.partition("?")[0]

    def url(self, ground: Ground) -> str:
        return self.path.format(entry_id=ground.entry_id, hypothesis=ground.hypothesis_id)


WRITES = [
    Route(
        "POST",
        CHAIN,
        lambda _: FIRST | {"subject": "Rights test for {{company}}"},
        journal=AuditAction.SALES_CHAIN_CHANGED,
    ),
    Route("POST", KB, lambda _: BRIEF, ok=201, journal=AuditAction.SALES_KB_CHANGED),
    Route(
        "PATCH",
        KB + "/{entry_id}",
        lambda _: {"active": False},
        journal=AuditAction.SALES_KB_CHANGED,
    ),
    Route(
        "POST",
        SENDER,
        lambda _: {"signature": "Ива Тестова", "physical_address": "Выдуманная ул., 7"},
        journal=AuditAction.SALES_KB_CHANGED,
    ),
    Route(
        "POST",
        QUEUE,
        lambda ground: {"hypothesis_id": ground.hypothesis_id, "limit": 5},
        journal=AuditAction.RUN_STARTED,
    ),
]
READS = [
    Route("GET", CHAIN),
    Route("POST", CHAIN + "/preview", lambda _: FIRST),
    Route("GET", KB),
    Route("GET", KB + "/preview"),
    Route("GET", SENDER),
    Route("GET", QUEUE + "?hypothesis={hypothesis}"),
]


def _name(route: Route) -> str:
    return f"{route.method} {route.template}"


@pytest.fixture
async def ground(session: AsyncSession, world: w.World, jobs: Any) -> Ground:
    entry = await kb.add(session, kb.entry(**PRICE), author="тест", author_id=None)
    await session.commit()
    return Ground(world.hypothesis_id, entry.id, jobs)


async def _signed(
    make_user: MakeUser, sign_in: SignIn, rights: dict[str, bool] | None
) -> tuple[UserModel, dict[str, str]]:
    user = await make_user(SELLER, permissions=rights)
    return user, bearer(await sign_in(SELLER))


async def _call(
    client: AsyncClient, route: Route, ground: Ground, headers: dict[str, str]
) -> Response:
    body = route.body(ground)
    return await client.request(route.method, route.url(ground), json=body, headers=headers)


async def _journal(session: AsyncSession, user: UserModel) -> list[AuditAction]:
    """Что учётка оставила в журнале, кроме входа."""
    found = await session.scalars(
        select(AuditLogModel.action)
        .where(AuditLogModel.user_id == user.id, AuditLogModel.action != AuditAction.LOGIN)
        .order_by(AuditLogModel.id)
    )
    return list(found)


# --- запись --------------------------------------------------------------------------------


@pytest.mark.parametrize("route", WRITES, ids=_name)
async def test_writing_with_only_the_sales_right_is_refused_in_words_about_sending(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    ground: Ground,
    route: Route,
) -> None:
    user, headers = await _signed(make_user, sign_in, ONLY_SALES)

    response = await _call(client, route, ground, headers)

    assert (response.status_code, response.json()["detail"]) == (403, NO_SEND)
    assert await _journal(session, user) == []
    assert ground.jobs.enqueued == []


@pytest.mark.parametrize("route", WRITES, ids=_name)
async def test_writing_with_only_the_send_right_is_refused_in_words_about_the_section(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    ground: Ground,
    route: Route,
) -> None:
    user, headers = await _signed(make_user, sign_in, ONLY_SEND)

    response = await _call(client, route, ground, headers)

    assert (response.status_code, response.json()["detail"]) == (403, NO_SALES)
    assert await _journal(session, user) == []
    assert ground.jobs.enqueued == []


@pytest.mark.parametrize("route", WRITES, ids=_name)
async def test_writing_with_both_rights_goes_through_with_the_author_in_the_journal(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    ground: Ground,
    route: Route,
) -> None:
    user, headers = await _signed(make_user, sign_in, BOTH)

    response = await _call(client, route, ground, headers)

    assert response.status_code == route.ok, response.text
    assert await _journal(session, user) == [route.journal]


# --- чтение и предпросмотр -----------------------------------------------------------------


@pytest.mark.parametrize("route", READS, ids=_name)
async def test_reading_and_preview_need_only_the_sales_right_and_write_nothing(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    ground: Ground,
    route: Route,
) -> None:
    user, headers = await _signed(make_user, sign_in, ONLY_SALES)

    response = await _call(client, route, ground, headers)

    assert response.status_code == 200, response.text
    assert await _journal(session, user) == []


async def test_the_tables_cover_every_route_of_the_four_tabs(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(TABS)
        for method in methods
    }

    assert in_app == {(route.method, route.template) for route in (*WRITES, *READS)}
    assert all(route.journal is not None for route in WRITES)
