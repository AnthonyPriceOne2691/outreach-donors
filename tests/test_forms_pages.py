"""Ручная очередь форм по странице (замечание 28.09.2026: «пагинация,
20 записей на странице»).

До этого очередь приходила одним списком до двухсот строк: сто первый
донор был недостижим, а страница на экране — невозможна. Проверяется
то, чем страница может соврать: стык страниц при равных DR (база вольна
отдавать такие строки в любом порядке), страница за концом и размер,
который называет сервер, а не экран.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.contacts import forms
from backend.features.core.domain import ContactStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("формы@site.com", role=UserRole.ADMIN)
    return await sign_in("формы@site.com")


async def _with_form(session: AsyncSession, host: str, *, dr: int, traffic: int) -> DonorModel:
    domain = await make_donor(session, host, dr=dr)
    donor = (
        (await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    donor.contact_status = ContactStatus.FORM_ONLY
    donor.org_traffic = traffic
    await session.flush()
    return donor


@pytest.fixture
async def queue_of_25(session: AsyncSession) -> list[str]:
    """Двадцать пять доноров с формой; у двадцати один и тот же DR —
    как в живой очереди, где подряд стоят 93, 93, 93. Возвращает хосты
    в том порядке, в каком их должен видеть человек."""
    made: list[tuple[int, int, int, str]] = []
    for number in range(25):
        dr = 93 if number < 20 else 40 + number
        traffic = 1000 * (number % 7)
        host = f"form-{number:02d}.example.test"
        donor = await _with_form(session, host, dr=dr, traffic=traffic)
        made.append((-dr, -traffic, donor.id, host))
    return [host for *_, host in sorted(made)]


class TestPages:
    async def test_first_page_is_the_server_size(
        self, client: AsyncClient, token: str, queue_of_25: list[str]
    ) -> None:
        response = await client.get("/api/contacts/forms?page=1", headers=bearer(token))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["page"] == 1
        assert body["limit"] == forms.PAGE_SIZE == 20
        assert body["total"] == 25
        assert [row["host"] for row in body["rows"]] == queue_of_25[:20]

    async def test_pages_meet_without_gaps_or_repeats(
        self, client: AsyncClient, token: str, queue_of_25: list[str]
    ) -> None:
        """При двадцати равных DR порядок без добавочного ключа отдаёт база:
        донор со стыка показывался бы на обеих страницах или ни на одной."""
        seen: list[str] = []
        for page in (1, 2):
            response = await client.get(f"/api/contacts/forms?page={page}", headers=bearer(token))
            seen += [row["host"] for row in response.json()["rows"]]

        assert seen == queue_of_25

    async def test_page_past_the_end_is_empty_not_a_refusal(
        self, client: AsyncClient, token: str, queue_of_25: list[str]
    ) -> None:
        """Ссылка на третью страницу, открытая после разбора очереди, —
        не поломка: экран узнаёт настоящее число страниц по `total`."""
        response = await client.get("/api/contacts/forms?page=3", headers=bearer(token))

        assert response.status_code == 200, response.text
        assert response.json()["rows"] == []
        assert response.json()["total"] == 25

    async def test_without_a_page_it_is_the_first(
        self, client: AsyncClient, token: str, queue_of_25: list[str]
    ) -> None:
        response = await client.get("/api/contacts/forms", headers=bearer(token))

        assert response.json()["page"] == 1
        assert len(response.json()["rows"]) == 20

    @pytest.mark.parametrize(
        "query", ["page=0", "page=-1", "page=abc", "limit=0", f"limit={forms.MAX_PAGE_SIZE + 1}"]
    )
    async def test_nonsense_is_refused(self, client: AsyncClient, token: str, query: str) -> None:
        response = await client.get(f"/api/contacts/forms?{query}", headers=bearer(token))

        assert response.status_code == 422

    async def test_strongest_first_then_traffic(self, session: AsyncSession) -> None:
        """Сильные сверху: при равном DR — у кого больше трафика."""
        await _with_form(session, "quiet.example.test", dr=70, traffic=10)
        await _with_form(session, "loud.example.test", dr=70, traffic=90_000)
        await _with_form(session, "strong.example.test", dr=88, traffic=5)

        rows = await forms.queue(session)

        assert [row.host for row in rows] == [
            "strong.example.test",
            "loud.example.test",
            "quiet.example.test",
        ]
