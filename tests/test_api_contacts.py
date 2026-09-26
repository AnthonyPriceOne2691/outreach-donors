"""Контакты по HTTP: права таблицей, поиск адреса одному донору и его задача.

До 25.09.2026 у группы «контакты» не было таблицы «кто пускается»: права
проверялись по тесту на маршрут, и новый маршрут мог уехать без проверки.
Здесь таблица есть — вместе с тестом, что она покрывает всю группу.

Поиск одному донору — не второе правило, а общее, суженное до донора.
Поэтому отдельно проверяется, что отказ словами (`refusal_of`) и сам отбор
(`_needs_contact`) не расходятся ни на одном сочетании состояний донора:
иначе карточка обещала бы поиск, в котором сервер откажет, или наоборот.

Задача проверяется телом, на настоящей базе, с подменённой сетью и платной
ступенью: вопрос — доходит ли она до лестницы с тем донором, которого
просили, и только с ним.
"""

from __future__ import annotations

import inspect
import itertools
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import mx
from backend.features.contacts import repository as contacts_repository
from backend.features.contacts.provider import Candidate, Quota
from backend.features.contacts.repository import (
    UNEXPLAINED,
    ContactRepository,
    refusal_of,
    search_refusal,
)
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.shared.queue import CONTACTS_JOB
from backend.workers import jobs
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ROOT = Path(__file__).resolve().parent.parent
TYPES_TS = ROOT / "frontend" / "src" / "api" / "types.ts"

#: Группа «контакты» целиком. Номер донора подставляется на месте.
ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/contacts", None, "view"),
    ("POST", "/api/contacts", {}, "run"),
    ("POST", "/api/contacts/donors/{donor}", None, "run"),
    ("GET", "/api/contacts/forms", None, "view"),
    ("POST", "/api/contacts/forms/{donor}/filled", {"email": "editor@form.example.test"}, "run"),
    ("POST", "/api/contacts/forms/{donor}/give-up", {}, "run"),
    (
        "POST",
        "/api/contacts/donors/{donor}/addresses",
        {"email": "ads@waiting.example.test"},
        "run",
    ),
    ("DELETE", "/api/contacts/donors/{donor}/addresses/{contact}", None, "run"),
]


class FakeQueue:
    """Очередь, которая ничего не выполняет: проверяется, что в неё
    положили, а не что rq работает."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.remembered: list[str] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.calls.append((job, args))
        return type("Job", (), {"id": "job-адрес"})()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.contacts.routes.runs_queue", lambda: fake)
    monkeypatch.setattr("backend.api.contacts.routes.remember_contacts_job", fake.remembered.append)
    monkeypatch.setattr("backend.api.contacts.routes.contacts_job_id", lambda: None)
    monkeypatch.setattr("backend.api.contacts.routes.workers_alive", lambda: 1)
    return fake


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    """Смотреть базу может, тратить — нет: поиск доходит до платной ступени."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"run": False})
    return await sign_in("зритель@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    status: DonorStatus = DonorStatus.SUITABLE,
    review: str | None = "accepted",
    contact_status: ContactStatus | None = None,
    attempted_at: datetime | None = None,
    dr: int | None = 40,
) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=status,
        review=review,
        dr=dr,
        contact_status=contact_status,
        contact_attempted_at=attempted_at,
    )
    session.add(donor)
    await session.flush()
    return donor


@pytest.fixture
async def waiting(session: AsyncSession) -> DonorModel:
    """Принятый человеком донор без адреса — ровно тот, кому ищут."""
    donor = await _donor(session, "waiting.example.test")
    await session.commit()
    return donor


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        waiting: DonorModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, path.format(donor=waiting.id, contact=1), json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_search_needs_its_own_right(
        self,
        client: AsyncClient,
        viewer_token: str,
        queue: FakeQueue,
        waiting: DonorModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Смотреть очередь и ставить поиск — разные права: лестница кончается
        платной ступенью, и поиск — такая же трата, как прогон."""
        response = await client.request(
            method,
            path.format(donor=waiting.id, contact=1),
            json=body,
            headers=bearer(viewer_token),
        )

        if permission == "run":
            assert response.status_code == 403
            assert "«run»" in response.json()["detail"]
            assert queue.calls == []
        else:
            assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/contacts")
            for method in methods
        }
        in_table = {
            (method, path.replace("{donor}", "{donor_id}").replace("{contact}", "{contact_id}"))
            for method, path, _, _ in ROUTES
        }
        assert in_app == in_table


class TestOneDonor:
    async def test_accepted_donor_without_address_goes_to_the_queue_alone(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, waiting: DonorModel
    ) -> None:
        """Та же задача, что у общего поиска, суженная до донора. Номер задачи
        не занимает место общего поиска: иначе экран списка показал бы исход
        одного донора как исход общего прохода."""
        response = await client.post(
            f"/api/contacts/donors/{waiting.id}", headers=bearer(operator_token)
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"job_id": "job-адрес", "pending": 1}
        assert queue.calls == [(CONTACTS_JOB, (1, False, False, waiting.id))]
        assert queue.remembered == []

    async def test_what_the_route_queues_the_job_accepts(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, waiting: DonorModel
    ) -> None:
        """Доводы задачи лежат в очереди дольше версии кода: ставящий и
        исполняющий обязаны совпасть по подписи, а не по договорённости."""
        await client.post(f"/api/contacts/donors/{waiting.id}", headers=bearer(operator_token))

        path, args = queue.calls[0]
        assert path == "backend.workers.jobs.find_contacts"
        bound = inspect.signature(jobs.find_contacts).bind(*args)
        assert bound.arguments["donor_id"] == waiting.id
        assert bound.arguments["limit"] == 1

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ({"status": DonorStatus.UNCHECKED, "dr": None}, "пороги ещё не проверяли"),
            ({"status": DonorStatus.UNSUITABLE}, "не прошёл пороги"),
            ({"review": None}, "после решения человека"),
            ({"review": "rejected"}, "отклонил человек"),
        ],
    )
    async def test_the_same_rule_refuses_with_words(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
        state: dict[str, Any],
        expected: str,
    ) -> None:
        """Сервер проверяет, экран объясняет: отказ — текстом, который
        говорит, какое условие не выполнено, и в очередь не уходит ничего."""
        donor = await _donor(session, "refused.example.test", **state)
        await session.commit()

        response = await client.post(
            f"/api/contacts/donors/{donor.id}", headers=bearer(operator_token)
        )

        assert response.status_code == 409
        assert expected in response.json()["detail"]
        assert queue.calls == []

    async def test_fresh_outcome_names_the_day_of_the_next_try(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        """Искали пять дней назад и не нашли: повтор прошёл бы ту же лестницу
        за те же деньги. Отказ называет день, когда станет можно."""
        attempted = datetime.now(UTC) - timedelta(days=5)
        donor = await _donor(
            session,
            "tried.example.test",
            contact_status=ContactStatus.NOT_FOUND,
            attempted_at=attempted,
        )
        await session.commit()

        response = await client.post(
            f"/api/contacts/donors/{donor.id}", headers=bearer(operator_token)
        )

        again = attempted + timedelta(days=contacts_cfg.CONTACT_TTL_DAYS)
        assert response.status_code == 409
        assert f"не раньше {again:%d.%m.%Y}" in response.json()["detail"]
        assert queue.calls == []

    @pytest.mark.parametrize(
        ("contact_status", "days_ago"),
        [
            # «Квота кончилась» — не «адреса нет»: не спросили, а не узнали.
            (ContactStatus.NO_QUOTA, 1),
            # Исход устарел — за ним снова идут, как и общий поиск.
            (ContactStatus.NOT_FOUND, 400),
        ],
    )
    async def test_what_the_general_search_would_take_is_taken(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
        contact_status: ContactStatus,
        days_ago: int,
    ) -> None:
        donor = await _donor(
            session,
            "again.example.test",
            contact_status=contact_status,
            attempted_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
        await session.commit()

        response = await client.post(
            f"/api/contacts/donors/{donor.id}", headers=bearer(operator_token)
        )

        assert response.status_code == 200, response.text
        assert len(queue.calls) == 1

    async def test_unknown_donor_is_not_found(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue
    ) -> None:
        response = await client.post("/api/contacts/donors/99999", headers=bearer(operator_token))

        assert response.status_code == 404
        assert response.json()["detail"] == "Донора №99999 нет"
        assert queue.calls == []

    async def test_card_says_the_same_before_the_press(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
        waiting: DonorModel,
    ) -> None:
        """Карточка знает отказ до нажатия, и это тот же текст, что вернул бы
        поиск: человек не должен узнавать о правиле отказом после."""
        unreviewed = await _donor(session, "unreviewed.example.test", review=None)
        await session.commit()

        card = await client.get(f"/api/donors/{unreviewed.id}", headers=bearer(operator_token))
        refused = await client.post(
            f"/api/contacts/donors/{unreviewed.id}", headers=bearer(operator_token)
        )
        ready = await client.get(f"/api/donors/{waiting.id}", headers=bearer(operator_token))

        assert card.json()["contact_refusal"] == refused.json()["detail"]
        assert ready.json()["contact_refusal"] is None


#: Момент, от которого считаются все сочетания ниже.
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
TTL = timedelta(days=contacts_cfg.CONTACT_TTL_DAYS)

#: Когда искали: не искали, недавно, давно и ровно на границе срока.
ATTEMPTS: dict[str, datetime | None] = {
    "не искали": None,
    "пять дней назад": NOW - timedelta(days=5),
    "давно": NOW - TTL - timedelta(days=20),
    "на границе": NOW - TTL,
}


class TestTheRuleIsSaidAloud:
    async def test_words_agree_with_the_query_on_every_state(self, session: AsyncSession) -> None:
        """Все сочетания: вердикт × решение человека × исход × давность.

        Решает запрос, объясняет `refusal_of`. Если они разойдутся, карточка
        покажет кнопку, а сервер откажет, — или спрячет кнопку там, где общий
        поиск донора взял бы сам.
        """
        states = itertools.product(
            DonorStatus,
            (None, "accepted", "rejected"),
            (None, *ContactStatus),
            ATTEMPTS.items(),
        )
        disagreements: list[str] = []
        for index, (status, review, outcome, (when, attempted)) in enumerate(states):
            donor = await _donor(
                session,
                f"state-{index}.example.test",
                status=status,
                review=review,
                contact_status=outcome,
                attempted_at=attempted,
            )
            waits = await ContactRepository(session, donor_id=donor.id).pending_count(now=NOW)
            said = refusal_of(donor, now=NOW)
            if bool(waits) != (said is None):
                disagreements.append(
                    f"{status.value}/{review}/{outcome}/{when}: запрос {waits}, слова {said!r}"
                )

        assert disagreements == []

    async def test_narrow_queue_sees_only_its_donor(self, session: AsyncSession) -> None:
        """Суженная очередь — та же очередь с одним условием больше."""
        strong = await _donor(session, "strong.example.test", dr=80)
        weak = await _donor(session, "weak.example.test", dr=20)

        assert await ContactRepository(session).pending_hosts(now=NOW) == [
            "strong.example.test",
            "weak.example.test",
        ]
        assert await ContactRepository(session, donor_id=weak.id).pending_hosts(now=NOW) == [
            "weak.example.test"
        ]
        assert await ContactRepository(session, donor_id=strong.id).pending_count(now=NOW) == 1

    async def test_unexplained_refusal_is_honest_and_loud(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Объяснение не нашло причины, а запрос отказал: отказ остаётся
        отказом — решает запрос, — а расхождение попадает в журнал."""
        donor = await _donor(session, "odd.example.test", review=None)
        monkeypatch.setattr(contacts_repository, "refusal_of", lambda *_a, **_k: None)

        with caplog.at_level(logging.WARNING):
            said = await search_refusal(session, donor, now=NOW)

        assert said == UNEXPLAINED
        assert "разошёлся" in caplog.text


class _Closable:
    async def dispose(self) -> None:
        return None


class _NoBrowser:
    """Браузер не поднимается: ступень выключена, а запуск стоил бы секунды."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeProvider:
    """Платная ступень. Считает, за какие домены заплатили бы."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.calls.append(host)
        return [Candidate(email=f"editor@{host}", source=ContactSource.PROVIDER, confidence=90)]


@pytest.fixture
def ladder(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> FakeProvider:
    """Сеть и платная ступень подменены; база — настоящая, та же, что у теста.

    Сайты отвечают пустыми страницами: адрес бесплатные ступени не находят,
    и лестница доходит до платной — ради неё подмена и стоит.
    """
    provider = FakeProvider()

    async def paid(_http: httpx.AsyncClient, _report: object) -> FakeProvider:
        return provider

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"", "/"}:
            return httpx.Response(
                200,
                text="<html><body>адреса нет</body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404, text="нет такой страницы")

    monkeypatch.setattr("backend.features.contacts.search._paid_step", paid)
    monkeypatch.setattr("backend.features.contacts.search.PlaywrightRenderer", _NoBrowser)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(site)),
    )
    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return provider


class TestTheJobBody:
    async def test_job_walks_only_the_donor_it_was_asked_for(
        self, session: AsyncSession, ladder: FakeProvider
    ) -> None:
        """В общей очереди сильный донор стоит первым. Задача с карточки
        слабого обязана пройти слабого — и только его."""
        strong = await _donor(session, "strong.example.test", dr=80)
        weak = await _donor(session, "weak.example.test", dr=20)
        await session.commit()

        report = await jobs._search_contacts(1, False, False, weak.id)

        assert ladder.calls == ["weak.example.test"]
        assert report["pending"] == 1
        assert report["walked"] == 1
        assert report["saved"] == 1

        await session.refresh(weak)
        await session.refresh(strong)
        assert weak.contact_status is ContactStatus.FOUND
        assert weak.contact_attempted_at is not None
        assert strong.contact_attempted_at is None
        contact = (
            await session.execute(
                select(ContactModel).where(ContactModel.domain_id == weak.domain_id)
            )
        ).scalar_one()
        assert contact.email == "editor@weak.example.test"
        assert contact.source is ContactSource.PROVIDER

    async def test_job_leaves_a_donor_that_no_longer_waits(
        self, session: AsyncSession, ladder: FakeProvider
    ) -> None:
        """Между нажатием и исполнением общий поиск успел найти адрес: задача
        проверяет правило заново и не платит второй раз за тот же домен."""
        donor = await _donor(
            session,
            "found.example.test",
            contact_status=ContactStatus.FOUND,
            attempted_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await session.commit()

        report = await jobs._search_contacts(1, False, False, donor.id)

        assert report["pending"] == 0
        assert report["walked"] == 0
        assert ladder.calls == []


class TestAddressFilter:
    async def test_no_address_includes_the_never_searched(
        self, client: AsyncClient, operator_token: str, session: AsyncSession
    ) -> None:
        """«Нет адреса» — это и «не нашли», и «ещё не искали». Голое отрицание
        в SQL теряло второе: у него исход пуст, а NULL не равен ничему."""
        await _donor(session, "found.example.test", contact_status=ContactStatus.FOUND, dr=50)
        await _donor(session, "missing.example.test", contact_status=ContactStatus.NOT_FOUND)
        await _donor(session, "never.example.test", dr=30)
        await session.commit()

        with_address = await client.get(
            "/api/donors?has_contact=true", headers=bearer(operator_token)
        )
        without = await client.get("/api/donors?has_contact=false", headers=bearer(operator_token))

        assert [row["host"] for row in with_address.json()["rows"]] == ["found.example.test"]
        assert [row["host"] for row in without.json()["rows"]] == [
            "missing.example.test",
            "never.example.test",
        ]
        assert without.json()["total"] == 2


def _union(name: str) -> set[str]:
    """Значения строкового объединения из `types.ts` фронта."""
    source = TYPES_TS.read_text(encoding="utf-8")
    found = re.search(rf"export type {name} =([^;]+);", source)
    assert found is not None, f"в {TYPES_TS.name} нет типа {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


class TestScreenSpeaksServerWords:
    """Слова экрана о доноре и адресе — те же значения, что отдаёт сервер.

    До 25.09.2026 фронт ждал источники адреса `mx`, `rdap`, `paid`, `form`,
    а сервер отдавал `whois` и `provider`: у 44 адресов базы разработки
    колонка «Откуда» в карточке была пустой, и сборка этого не видела —
    тип врал, а подпись к неизвестному значению молча не находилась.
    """

    @pytest.mark.parametrize(
        ("name", "values"),
        [
            ("DonorStatus", {status.value for status in DonorStatus}),
            ("ContactStatus", {status.value for status in ContactStatus}),
            ("ContactSource", {source.value for source in ContactSource}),
        ],
    )
    def test_front_knows_exactly_the_server_values(self, name: str, values: set[str]) -> None:
        assert _union(name) == values
