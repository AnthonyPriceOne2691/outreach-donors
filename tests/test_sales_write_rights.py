"""Запись текстов продаж — с правами `sales` и `send`, как в ядре; смотреть — с одним `sales`.

Цепочку писем, записи базы знаний, отправителя, сборку очереди, лида и стоп-лист продаж меняет
только тот, кому можно отправлять, — как письмо ядра (`api/letters/routes.py`): текст уходит
адресату, база знаний — опора судьи, имя и язык лида решают, какое письмо и кому, стоп-лист —
кому продажи не пишут; обычная учётка не должна превращаться в рассылку. Чтение и предпросмотр
ничего не пишут и остаются под `sales`.

Каждый маршрут записи — тремя учётками: с одним `sales` — 403 словами о `send`, с одним `send` —
403 словами о `sales`, и в обоих отказах журнал пуст; с обоими правами запись проходит и ложится
в журнал с автором. Таблицы сверяются с приложением: новый маршрут этих вкладок без строки
здесь — красный тест, и маршрут записи любой вкладки раздела — тоже, если его нет ни здесь, ни
среди записей под другими правами (`ELSEWHERE`). Тексты, подписи и адреса выдуманы
(`*.example.test`).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import kb, stoplist
from backend.features.sales.models import SalesStoplistModel
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
NO_SALES = no_right(Permission.SALES)
NO_SEND = no_right(Permission.SEND)

#: Учётки по правам. Оператору `sales` положено ролью, `send` выдают поимённо.
ONLY_SALES: dict[str, bool] | None = None
ONLY_SEND = {"sales": False, "send": True}
BOTH = {"send": True}

CHAIN = "/api/sales/chain"
KB = "/api/sales/kb"
SENDER = "/api/sales/sender"
QUEUE = "/api/sales/queue"
LEADS = "/api/sales/leads"
STOPLIST = "/api/sales/stoplist"
#: Пути вкладок с записью под `send`: всё, что приложение отдаёт под ними, стоит в таблицах ниже.
TABS = (CHAIN, KB, SENDER, QUEUE, LEADS, STOPLIST)
#: Записи раздела под другими правами — их права держат свои тесты: загрузка базы и гипотеза —
#: одно `sales` (`test_api_sales_intake.py`, `test_sales_hypothesis_api.py`): в письма они не
#: уходят; очистка — `sales` и `run` (`test_sales_clean_api.py`): платная проверка адресов.
ELSEWHERE = {
    ("POST", "/api/sales/import"),
    ("POST", "/api/sales/import/preview"),
    ("POST", "/api/sales/hypotheses"),
    ("POST", "/api/sales/clean"),
    # Сбор базы (6.1): смета, решение по сайту и описание сегмента — `sales`, запуск и
    # продолжение — ещё `run` (платные выдача, модель и поиск адресов, как очистка), — права
    # держит `test_sales_segment_api.py`; в письма ничего из них не уходит.
    ("PATCH", "/api/sales/hypotheses/{}"),
    ("POST", "/api/sales/segment/estimate"),
    ("POST", "/api/sales/segment/runs"),
    ("POST", "/api/sales/segment/runs/{}/resume"),
    ("POST", "/api/sales/segment/candidates/{}/decision"),
}

PRICE = {
    "kind": "price_policy",
    "language": "ru",
    "title": "Цена аудита",
    "text": "Цену называем после короткого созвона.",
}
BRIEF = {"kind": "brief", "language": "ru", "title": "Кто мы", "text": "Студия примеров."}


@dataclass(frozen=True, slots=True)
class Ground:
    """Мир, где проходит каждый маршрут: продажи подключены, есть гипотеза, запись базы знаний,
    лид и запись стоп-листа продаж."""

    hypothesis_id: int
    entry_id: int
    jobs: Any
    lead_id: int
    #: Запись стоп-листа продаж — её снимает маршрут снятия.
    stop_id: int

    @property
    def ids(self) -> dict[str, int]:
        """Номера мира — по именам подстановок в путях таблиц."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "entry_id": self.entry_id,
            "lead_id": self.lead_id,
            "stop_id": self.stop_id,
        }


@dataclass(frozen=True, slots=True)
class Route:
    """Маршрут вкладки: метод, путь приложения (с адресом запроса), тело и след в журнале."""

    method: str
    #: Путь приложения; номера подставляет мир по именам `Ground.ids` — с приложением
    #: сверяется форма пути, без имён подстановок.
    path: str
    body: Callable[[Ground], dict[str, Any] | None] = lambda _: None
    ok: int = 200
    #: Что запись кладёт в журнал от имени автора; чтение не кладёт ничего.
    journal: AuditAction | None = None
    #: Тело — полями формы, как у стоп-листа продаж (`Form`), а не JSON.
    form: bool = False

    @property
    def template(self) -> str:
        return _shape(self.path.partition("?")[0])

    def url(self, ground: Ground) -> str:
        return self.path.format(**ground.ids)


def _shape(path: str) -> str:
    """Путь без имён подстановок: `/kb/{entry_id}` и `/kb/{id}` — один маршрут."""
    return re.sub(r"\{[^}]*\}", "{}", path)


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
    Route(
        "PATCH",
        LEADS + "/{lead_id}",
        lambda _: {"name": "Ива Тестова"},
        journal=AuditAction.SALES_LEADS_IMPORTED,
    ),
    Route(
        "POST",
        STOPLIST,
        lambda _: {"text": "client.example.test", "note": "клиенты"},
        journal=AuditAction.SUPPRESSION_ADDED,
        form=True,
    ),
    Route("POST", STOPLIST + "/{stop_id}/remove", journal=AuditAction.SUPPRESSION_REMOVED),
]
READS = [
    Route("GET", CHAIN),
    Route("POST", CHAIN + "/preview", lambda _: FIRST),
    Route("GET", KB),
    Route("GET", KB + "/preview"),
    Route("GET", SENDER),
    Route("GET", QUEUE + "?hypothesis={hypothesis_id}"),
    Route("GET", LEADS),
    #: Лиду мира ещё не писали — добивок нет, ответ 404 словами, а не 403: права пустили.
    Route("GET", LEADS + "/{lead_id}/followups", ok=404),
    Route("GET", STOPLIST),
]


def _name(route: Route) -> str:
    return f"{route.method} {route.template}"


@pytest.fixture
async def ground(session: AsyncSession, world: w.World, jobs: Any) -> Ground:
    entry = await kb.add(session, kb.entry(**PRICE), author="тест", author_id=None)
    lead = await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    entries = stoplist.read_text("partner.example.test")
    await stoplist.add(session, entries, author="тест", note=None)
    stop = await session.scalar(select(SalesStoplistModel.id))
    assert stop is not None
    await session.commit()
    return Ground(world.hypothesis_id, entry.id, jobs, lead.id, stop)


async def _signed(
    make_user: MakeUser, sign_in: SignIn, rights: dict[str, bool] | None
) -> tuple[UserModel, dict[str, str]]:
    user = await make_user(SELLER, permissions=rights)
    return user, bearer(await sign_in(SELLER))


async def _call(
    client: AsyncClient, route: Route, ground: Ground, headers: dict[str, str]
) -> Response:
    body = route.body(ground)
    if route.form:
        return await client.request(route.method, route.url(ground), data=body, headers=headers)
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

    assert response.status_code == route.ok, response.text
    assert await _journal(session, user) == []


def _routes(app: FastAPI, prefixes: tuple[str, ...]) -> set[tuple[str, str]]:
    """Маршруты приложения под путями `prefixes`: метод и форма пути."""
    return {
        (method.upper(), _shape(path))
        for path, methods in app.openapi()["paths"].items()
        if path.startswith(prefixes)
        for method in methods
    }


async def test_the_tables_cover_every_route_of_the_tabs(api_app: FastAPI) -> None:
    in_app = _routes(api_app, TABS)

    assert in_app == {(route.method, route.template) for route in (*WRITES, *READS)}
    assert all(route.journal is not None for route in WRITES)


async def test_every_write_of_the_section_is_in_a_table(api_app: FastAPI) -> None:
    """Маршрут записи на вкладке, которой нет в `TABS`, не уйдёт мимо таблиц: так правка лида и
    стоп-лист продаж жили вне проверки, пока в `TABS` были четыре вкладки."""
    writes = {route for route in _routes(api_app, ("/api/sales/",)) if route[0] != "GET"}
    tabled = {(route.method, route.template) for route in (*WRITES, *READS)}

    assert writes == {route for route in tabled if route[0] != "GET"} | ELSEWHERE
