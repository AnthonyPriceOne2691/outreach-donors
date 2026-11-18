"""Поиск лидов продаж по домену IDN письменностью сайта: `GET /sales/leads?search=…` (18.11.2026).

База хранит домены IDN в ASCII: «пример.рф» — `xn--e1afmkfd.xn--p1ai`. С 18.11.2026 экран лидов
показывает сайт и адрес письменностью сайта (`frontend/src/hostName.ts`), и в поиск человек
набирает то, что видит, — «пример.рф», а не `xn--…`. Поиск лидов (`sales/browse._by_search`) ищет
такое набранное и в ASCII-форме (`text_search.contains_host`) — в двух колонках: в адресе лида и в
домене его сайта. Здесь — что каждая из двух находит лида сама: у одного лида IDN — только сайт, у
другого — только домен адреса, и без своей половины условия каждый из них пропал бы из поиска.

ASCII ищется, как прежде. Обрывок метки («прим») ASCII-форму не находит — известное ограничение:
punycode кодирует метку целиком, и у обрывка своя запись, а не кусок чужой. Само правило поиска —
в `tests/test_text_search.py`. Люди, компании и домены выдуманы (`*.example.test`, «пример.рф»).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import LeadSource, SalesHypothesisModel, SalesLeadModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
LEADS = "/api/sales/leads"

#: Лид с сайтом IDN — «пример.рф» (`xn--e1afmkfd.xn--p1ai`); адрес — на домене ASCII.
IDN_SITE = "ops@mail.example.test"
#: Лид с адресом на домене IDN — «сад.example.test»; сайт — ASCII (`shop.example.test`).
IDN_MAIL = "info@xn--80ai1b.example.test"


@pytest.fixture
async def leads(session: AsyncSession) -> None:
    """Два лида одной гипотезы; IDN у каждого — в своей колонке, имени и компании нет: найти
    лида может только та колонка, ради которой он заведён."""
    hypothesis = SalesHypothesisModel(name="интернет-магазины · RU")
    site = DomainModel(host="xn--e1afmkfd.xn--p1ai")
    shop = DomainModel(host="shop.example.test")
    session.add_all([hypothesis, site, shop])
    await session.flush()
    for domain, email in ((site, IDN_SITE), (shop, IDN_MAIL)):
        session.add(
            SalesLeadModel(
                hypothesis_id=hypothesis.id,
                domain_id=domain.id,
                email=email,
                source=LeadSource.IMPORT,
            )
        )
    await session.commit()


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _found(
    client: AsyncClient, headers: dict[str, str], typed: str | None = None
) -> list[str]:
    """Адреса найденных лидов; без `typed` — все лиды экрана."""
    params = {} if typed is None else {"search": typed}
    response = await client.get(LEADS, params=params, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    emails = sorted(row["email"] for row in body["rows"])
    # Число под фильтром — тем же условием, что строки: иначе «ничего» читалось бы как «база пуста».
    assert body["total"] == len(emails)
    return emails


@pytest.mark.usefixtures("leads")
@pytest.mark.parametrize("typed", ["пример", "Пример.РФ"])
async def test_the_site_typed_in_its_script_finds_its_lead(
    client: AsyncClient, headers: dict[str, str], typed: str
) -> None:
    """Сайт лида — `DomainModel.host`: адрес этого лида — ASCII, и найти его может только сайт."""
    assert await _found(client, headers, typed) == [IDN_SITE]


@pytest.mark.usefixtures("leads")
@pytest.mark.parametrize("typed", ["сад.example.test", "info@сад.example.test"])
async def test_the_mail_domain_typed_in_its_script_finds_its_lead(
    client: AsyncClient, headers: dict[str, str], typed: str
) -> None:
    """Адрес лида — `SalesLeadModel.email`: сайт этого лида — ASCII, и найти его может только адрес."""
    assert await _found(client, headers, typed) == [IDN_MAIL]


@pytest.mark.usefixtures("leads")
@pytest.mark.parametrize(("typed", "lead"), [("ops@mail", IDN_SITE), ("shop.example", IDN_MAIL)])
async def test_ascii_typed_is_found_as_before(
    client: AsyncClient, headers: dict[str, str], typed: str, lead: str
) -> None:
    """Набранное в ASCII — по-прежнему подстрока адреса и домена, второй формы у него нет."""
    assert await _found(client, headers, typed) == [lead]


@pytest.mark.usefixtures("leads")
async def test_a_fragment_of_a_label_does_not_find_the_ascii_form(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    """Известное ограничение (`contains_host`): «прим» в ASCII — своя метка (`xn--h1aihe`), а не
    кусок записи «пример» (`xn--e1afmkfd`). Лиды на экране есть — не находит именно обрывок."""
    assert await _found(client, headers) == sorted([IDN_SITE, IDN_MAIL])
    assert await _found(client, headers, "прим") == []
