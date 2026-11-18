"""Поиск по домену IDN письменностью сайта на экранах доноров и рекламодателей (18.11.2026).

База хранит домены IDN в ASCII: «пример.рф» — `xn--e1afmkfd.xn--p1ai`. С 18.11.2026 экраны
показывают их письменностью сайта (`frontend/src/hostName.ts`), и в поиск человек набирает то, что
видит, — «пример», а не `xn--…`. Условие поиска по домену каждый экран строит сам, и каждый теперь —
общим `text_search.contains_host`: набранное ищется ещё и в ASCII-форме. Здесь — что поле поиска
каждого экрана до этого условия доходит, через адрес экрана, как его спрашивает фронт: на экране
домен IDN и домен ASCII, «пример» и «Пример.рф» находят только первый, ASCII — как прежде, и число
под фильтром — тем же условием, что строки.

Экраны: «Доноры» (`donors/table_filters.narrowed`), «Отбор» (`donors/selection._by_search`),
«Рекламодатели» (`crawl/advertiser_list._narrow`) и вкладка «Обход доноров»
(`crawl/donor_page._where`). Само правило поиска — в `tests/test_text_search.py`, поиск лидов
продаж — в `tests/test_sales_idn_search.py`. Домены выдуманы (`*.example.test`, «пример.рф»).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from backend.api.crawls import routes as crawl_routes
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.stage2_rows import promoted

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

OPERATOR = "operator@ours.example.test"
#: «пример.рф» — так его хранит база.
IDN = "xn--e1afmkfd.xn--p1ai"
ASCII = "shop.example.test"


@dataclass(frozen=True, slots=True)
class Screen:
    """Экран с полем поиска по домену: адрес его строк и чем завести на нём строку домена."""

    url: str
    seed: Callable[[AsyncSession, str], Awaitable[object]]


#: Донор, принятый человеком и годный по порогам (`make_donor`), стоит на трёх экранах сразу:
#: в таблице доноров, на вкладке «приняты» отбора и на вкладке обхода — та показывает и донора
#: без цены. Рекламодатель — переводом по ссылке на доноре (`promoted`): такому пишем всегда.
SCREENS = {
    "donors": Screen("/api/donors", make_donor),
    "selection": Screen("/api/selection", make_donor),
    "advertisers": Screen("/api/advertisers/list", promoted),
    "crawl-donors": Screen("/api/crawls/donors", make_donor),
}


@pytest.fixture(autouse=True)
def _queue_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вкладка «Обход доноров» спрашивает и очередь обходчиков (Redis): поиск не о ней, ответы —
    подставные, как у `tests/test_crawl_donor_tab.py`."""
    monkeypatch.setattr(crawl_routes, "workers_alive", lambda **_: 1)
    monkeypatch.setattr(crawl_routes, "job_positions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(crawl_routes, "crawlers_held", lambda **_: None)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user(OPERATOR, role=UserRole.OPERATOR)
    return await sign_in(OPERATOR)


async def _hosts(client: AsyncClient, token: str, url: str, **params: str) -> list[str]:
    """Домены строк экрана под фильтром."""
    response = await client.get(url, params=params, headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    hosts = sorted(row["host"] for row in body["rows"])
    # Число под фильтром — тем же условием, что строки: иначе «ничего» читалось бы как «пусто».
    assert body["total"] == len(hosts)
    return hosts


@pytest.mark.parametrize("screen", list(SCREENS.values()), ids=list(SCREENS))
async def test_a_name_typed_in_its_script_finds_the_idn_host(
    client: AsyncClient, session: AsyncSession, token: str, screen: Screen
) -> None:
    await screen.seed(session, IDN)
    await screen.seed(session, ASCII)
    await session.commit()

    # Без поиска на экране оба: «нашёлся только IDN» — дело поиска, а не выборки экрана.
    assert await _hosts(client, token, screen.url) == sorted([IDN, ASCII])
    # Имя письменностью сайта — целой меткой и целиком, в любом регистре.
    assert await _hosts(client, token, screen.url, search="пример") == [IDN]
    assert await _hosts(client, token, screen.url, search="Пример.рф") == [IDN]
    # ASCII — как прежде.
    assert await _hosts(client, token, screen.url, search="shop") == [ASCII]
