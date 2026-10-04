"""Раздел «Продажи» через API — срез 1.5: гипотезы со счётчиками, лиды с фильтрами, 403.

Списки читают базу дерева: лиды заводятся строками `sales_leads` в тех состояниях
и с теми кодами причин, которые пишет очистка (1.4). Люди и компании выдуманы,
домены — `*.example.test`. Отказы — словами сервера: экран показывает `detail` целиком.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
HYPOTHESES = "/api/sales/hypotheses"
LEADS = "/api/sales/leads"
NO_RIGHT = "Действие «sales» недоступно этой учётке"

STATES = {status.value for status in LeadStatus}
REASONS = {reason.value for reason in RejectionReason}


async def _headers(make_user: MakeUser, sign_in: SignIn, *, sales: bool = True) -> dict[str, str]:
    await make_user(SELLER, permissions=None if sales else {"sales": False})
    return bearer(await sign_in(SELLER))


async def _lead(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    email: str,
    *,
    host: str | None = None,
    status: LeadStatus = LeadStatus.NEW,
    rejected: tuple[RejectionReason, str] | None = None,
    name: str | None = None,
    company: str | None = None,
) -> SalesLeadModel:
    """Лид в базе — так, как его оставили загрузка и очистка: `rejected` — код
    причины и её слова. Домен компании — домен адреса, если не сказано иное;
    строка `domains` заводится здесь же."""
    domain = DomainModel(host=host or email.rpartition("@")[2])
    session.add(domain)
    await session.flush()
    reason, note = rejected if rejected is not None else (None, None)
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=email,
        name=name,
        company=company,
        source=LeadSource.IMPORT,
        status=LeadStatus.REJECTED if rejected is not None else status,
        rejection_reason=None if reason is None else reason.value,
        cleaning_note=note,
    )
    session.add(lead)
    await session.flush()
    return lead


@dataclass(frozen=True, slots=True)
class Field:
    """Две гипотезы и пять лидов: по одному на каждый исход экрана."""

    en: SalesHypothesisModel
    ru: SalesHypothesisModel
    leads: dict[str, SalesLeadModel]


@pytest.fixture
async def field(session: AsyncSession) -> Field:
    en = SalesHypothesisModel(name="сайты EN", description="редакции и блоги")
    ru = SalesHypothesisModel(name="сервисы RU")
    session.add_all([en, ru])
    await session.flush()
    leads = {
        "ivan": await _lead(
            session, en, "ivan@acme.example.test", name="Иван Петров", company="Acme"
        ),
        "maria": await _lead(
            session, en, "maria@beta.example.test", status=LeadStatus.READY, company="Beta"
        ),
        "twin": await _lead(
            session,
            en,
            "twin@acme.example.test",
            host="acme-twin.example.test",
            rejected=(RejectionReason.DUPLICATE, "дубль: адрес уже у лида №1"),
        ),
        "dead": await _lead(
            session,
            en,
            "dead@gamma.example.test",
            rejected=(
                RejectionReason.NO_MAIL,
                "домен не принимает почту (нет ни MX, ни A): gamma.example.test",
            ),
        ),
        "olga": await _lead(session, ru, "olga@delta.example.test", name="Ольга"),
    }
    await session.commit()
    return Field(en, ru, leads)


def _emails(body: dict[str, Any]) -> list[str]:
    return [row["email"] for row in body["rows"]]


async def test_hypotheses_are_listed_with_lead_counts_per_state(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field
) -> None:
    response = await client.get(HYPOTHESES, headers=await _headers(make_user, sign_in))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    en, ru = body["rows"]
    assert (en["id"], en["name"], en["description"]) == (
        field.en.id,
        "сайты EN",
        "редакции и блоги",
    )
    # Все три состояния названы, хоть и нулём: по ключам экран строит плитки.
    assert (en["leads"], en["total"]) == ({"new": 1, "ready": 1, "rejected": 2}, 4)
    assert (ru["leads"], ru["total"]) == ({"new": 1, "ready": 0, "rejected": 0}, 1)
    assert ru["created_at"] is not None


async def test_leads_come_newest_first_with_hypothesis_company_domain_and_counts(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field
) -> None:
    response = await client.get(LEADS, headers=await _headers(make_user, sign_in))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["total"], body["page"], body["limit"]) == (5, 1, 20)
    assert _emails(body) == [
        "olga@delta.example.test",
        "dead@gamma.example.test",
        "twin@acme.example.test",
        "maria@beta.example.test",
        "ivan@acme.example.test",
    ]
    twin = body["rows"][2]
    assert {
        key: twin[key]
        for key in ("name", "company", "host", "hypothesis", "status", "rejection_reason")
    } == {
        "name": None,
        "company": None,
        "host": "acme-twin.example.test",
        "hypothesis": "сайты EN",
        "status": "rejected",
        "rejection_reason": "duplicate",
    }
    assert twin["cleaning_note"] == "дубль: адрес уже у лида №1"
    assert twin["hypothesis_id"] == field.en.id
    # Сводка — по всем лидам, не по странице; каждый код назван, хоть и нулём.
    assert body["states"] == {"new": 2, "ready": 1, "rejected": 2}
    assert set(body["reasons"]) == REASONS
    assert {code: n for code, n in body["reasons"].items() if n} == {"duplicate": 1, "no_mail": 1}


@pytest.mark.parametrize(
    ("query", "emails"),
    [
        ("state=rejected&reason=duplicate", ["twin@acme.example.test"]),  # A2
        ("state=rejected", ["dead@gamma.example.test", "twin@acme.example.test"]),
        ("state=new", ["olga@delta.example.test", "ivan@acme.example.test"]),
        ("state=ready", ["maria@beta.example.test"]),
        ("reason=no_mail", ["dead@gamma.example.test"]),
    ],
)
async def test_a2_state_and_reason_narrow_the_list_as_the_address_says(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    field: Field,
    query: str,
    emails: list[str],
) -> None:
    # A2 — пример спеки, серверная сторона
    response = await client.get(f"{LEADS}?{query}", headers=await _headers(make_user, sign_in))

    assert response.status_code == 200, response.text
    assert (_emails(response.json()), response.json()["total"]) == (emails, len(emails))
    # Сводка не сужается фильтром: по ней видно, сколько всего и где.
    assert response.json()["states"] == {"new": 2, "ready": 1, "rejected": 2}


async def test_hypothesis_and_search_filters_find_by_address_name_company_and_domain(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field
) -> None:
    headers = await _headers(make_user, sign_in)

    async def found(query: str) -> list[str]:
        response = await client.get(f"{LEADS}?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return _emails(response.json())

    assert await found(f"hypothesis={field.ru.id}") == ["olga@delta.example.test"]
    # Регистр не важен; «acme» есть в адресе у двоих и в домене у одного из них.
    assert await found("search=ACME") == ["twin@acme.example.test", "ivan@acme.example.test"]
    assert await found("search=петров") == ["ivan@acme.example.test"]
    assert await found("search=beta") == ["maria@beta.example.test"]
    assert await found("search=acme-twin") == ["twin@acme.example.test"]
    assert await found(f"hypothesis={field.en.id}&state=new") == ["ivan@acme.example.test"]


async def test_twenty_per_page_and_the_server_names_the_size(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    field: Field,
) -> None:
    for n in range(20):
        await _lead(session, field.ru, f"lead{n}@firm{n}.example.test")
    await session.commit()
    headers = await _headers(make_user, sign_in)

    first = (await client.get(LEADS, headers=headers)).json()
    second = (await client.get(f"{LEADS}?page=2", headers=headers)).json()
    beyond = (await client.get(f"{LEADS}?page=9", headers=headers)).json()
    narrow = (await client.get(f"{LEADS}?limit=5", headers=headers)).json()

    assert (len(first["rows"]), first["total"], first["page"], first["limit"]) == (20, 25, 1, 20)
    assert (len(second["rows"]), second["page"]) == (5, 2)
    assert second["rows"][-1]["email"] == "ivan@acme.example.test"  # самый старый — последним
    # Страница за концом — пустая, с настоящим числом: экран по нему вернётся
    # на последнюю настоящую, а не покажет «ничего не нашлось».
    assert (beyond["rows"], beyond["total"]) == ([], 25)
    assert (len(narrow["rows"]), narrow["limit"]) == (5, 5)


async def test_unknown_reason_or_hypothesis_matches_nothing_instead_of_refusing(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field
) -> None:
    """Адрес экрана правят руками: незнакомый код причины или номер гипотезы
    из устаревшей ссылки — пустая таблица с условием словами, а не отказ."""
    headers = await _headers(make_user, sign_in)

    for query in ("reason=robots", "hypothesis=999999"):
        response = await client.get(f"{LEADS}?{query}", headers=headers)
        assert (response.status_code, response.json()["total"]) == (200, 0), query


async def test_state_outside_the_enum_is_refused_by_the_schema(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field
) -> None:
    """Состояние — тип базы, а не строка: негодное значение отвергает схема запроса.
    Экран таких не шлёт — незнакомое в адресе он читает как «не сужать»."""
    response = await client.get(f"{LEADS}?state=robots", headers=await _headers(make_user, sign_in))

    assert response.status_code == 422


@pytest.mark.parametrize("path", [HYPOTHESES, LEADS])
async def test_a4_without_the_sales_right_the_lists_refuse_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, field: Field, path: str
) -> None:  # A4
    headers = await _headers(make_user, sign_in, sales=False)

    response = await client.get(path, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
