"""История прогонов по страницам и выбор прогонов для рассылки — по HTTP.

Замечание 25.09.2026: «пагинация, максимум 10 прогонов на странице».
Страница считается на сервере, а не срезом на экране: экран получает
номер страницы, размер и сколько прогонов всего. Второе, что здесь
проверяется, — страница истории не урезает выбор прогонов на экране писем:
у него свой маршрут и все прогоны, из которых есть что собрать.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.review.candidates import Decision
from backend.features.runs.browse import PAGE_SIZE
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _runs(session: AsyncSession, count: int) -> list[RunModel]:
    """Столько законченных прогонов, по порядку заведения."""
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    made = [
        await repository.create_run(
            stage=Stage.DONORS,
            settings_id=settings.id,
            keywords=["guest post saas"],
            country="us",
            status=RunStatus.DONE,
        )
        for _ in range(count)
    ]
    await session.commit()
    return made


async def _accept(session: AsyncSession, run: RunModel, host: str) -> None:
    """Человек принял домен из очереди этого прогона."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(
        RunCandidateModel(run_id=run.id, domain_id=domain.id, status=Decision.ACCEPTED.value)
    )
    await session.commit()


async def _page(client: AsyncClient, token: str, query: str = "") -> dict[str, object]:
    response = await client.get(f"/api/runs{query}", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _ids(body: dict[str, object]) -> list[int]:
    runs = body["runs"]
    assert isinstance(runs, list)
    return [run["id"] for run in runs]


class TestHistoryPages:
    async def test_first_page_is_the_newest_ten_and_names_the_whole(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        runs = await _runs(session, PAGE_SIZE + 2)
        newest_first = [run.id for run in reversed(runs)]

        body = await _page(client, token)

        assert _ids(body) == newest_first[:PAGE_SIZE]
        # Размер страницы называет сервер: у экрана своей копии числа нет.
        assert body["limit"] == PAGE_SIZE == 10
        assert body["total"] == PAGE_SIZE + 2
        assert body["page"] == 1

    async def test_second_page_continues_where_the_first_ended(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        runs = await _runs(session, PAGE_SIZE + 2)
        newest_first = [run.id for run in reversed(runs)]

        first = await _page(client, token, "?page=1")
        second = await _page(client, token, "?page=2")

        assert _ids(second) == newest_first[PAGE_SIZE:]
        assert not set(_ids(first)) & set(_ids(second)), "прогон не встаёт на две страницы"
        assert second["total"] == PAGE_SIZE + 2

    async def test_page_past_the_end_is_empty_but_knows_the_total(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Ссылка на страницу, которой уже нет, — не отказ: по `total` экран
        узнаёт, сколько страниц есть, и уходит на последнюю."""
        await _runs(session, 3)

        body = await _page(client, token, "?page=5")

        assert _ids(body) == []
        assert body["total"] == 3
        assert body["page"] == 5

    async def test_exactly_one_page_is_one_page(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Десять прогонов — одна страница: переключателю на экране нечего
        переключать, и по этим числам он не отрисовывается."""
        await _runs(session, PAGE_SIZE)

        body = await _page(client, token)

        assert len(_ids(body)) == PAGE_SIZE
        assert body["total"] == body["limit"]
        assert _ids(await _page(client, token, "?page=2")) == []

    @pytest.mark.parametrize("query", ["?page=0", "?page=-1", "?limit=0", "?limit=51"])
    async def test_nonsense_pages_are_refused(
        self, client: AsyncClient, token: str, query: str
    ) -> None:
        response = await client.get(f"/api/runs{query}", headers=bearer(token))

        assert response.status_code == 422

    async def test_workers_are_still_reported(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Страницы не отменяют главного, ради чего экран спрашивает очередь:
        задачу, которую некому взять, видно и на пустой истории."""
        monkeypatch.setattr("backend.api.runs.routes.workers_alive", lambda: 0)

        body = await _page(client, token)

        assert body["workers"] == 0
        assert body["total"] == 0


class TestRunsForLetters:
    async def test_every_run_with_accepted_donors_is_offered(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Выбор прогонов на экране писем брал общий список — сначала первые
        тридцать, со страницами были бы первые десять. Прогон с принятыми
        донорами, ушедший на вторую страницу, пропал бы из выбора молча."""
        runs = await _runs(session, PAGE_SIZE + 3)
        oldest, middle, newest = runs[0], runs[5], runs[-1]
        for index, run in enumerate((oldest, middle, newest)):
            await _accept(session, run, f"donor-{index}.example.test")

        response = await client.get("/api/runs/with-accepted", headers=bearer(token))

        assert response.status_code == 200, response.text
        offered = [card["id"] for card in response.json()]
        assert offered == [newest.id, middle.id, oldest.id]
        assert oldest.id not in _ids(await _page(client, token)), "старый — за первой страницей"
        assert response.json()[-1]["queue"] == {"accepted": 1}

    async def test_runs_without_accepted_are_not_offered(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Прогон, где никого не приняли, собрать не из чего: в выборе он
        выглядел бы поломкой."""
        runs = await _runs(session, 2)
        domain = DomainModel(host="pending.example.test")
        session.add(domain)
        await session.flush()
        session.add(
            RunCandidateModel(run_id=runs[0].id, domain_id=domain.id, status=Decision.PENDING.value)
        )
        await session.commit()

        response = await client.get("/api/runs/with-accepted", headers=bearer(token))

        assert response.json() == []


class TestReasonOnTheCard:
    async def test_stored_class_name_does_not_reach_the_screen(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Прогоны №19–№23 на рабочем сервере хранят причину с именем класса.
        Запись не переписывается — карточка показывает её чисто."""
        (run,) = await _runs(session, 1)
        raw = (
            "остановлен разбором: задача упала: backend.features.runs.budget.CapExceededError: "
            "Прогон обойдётся в 3480 юнитов, доступно 3000. Новых доменов 61 из 62; "
            "сократите список ключей или поднимите кап., продолжений 2 из 2, молчание 901 с"
        )
        run.status = RunStatus.STOPPED
        run.stats = {"причина": raw}
        await session.commit()
        # Отметку о жизни база ставит сама при записи — перечитываем, иначе
        # маршрут в той же сессии полез бы за ней синхронно.
        await session.refresh(run)

        card = (await client.get(f"/api/runs/{run.id}", headers=bearer(token))).json()
        listed = (await _page(client, token))["runs"]

        assert "CapExceededError" not in card["reason"]
        assert card["reason"].startswith("остановлен разбором: задача упала: Прогон обойдётся")
        assert card["reason"].endswith("продолжений 2 из 2, молчание 901 с")
        assert isinstance(listed, list)
        assert listed[0]["reason"] == card["reason"]
        # Сырой текст на месте: чинится показ, а не запись.
        await session.refresh(run)
        assert run.stats["причина"] == raw

    async def test_run_without_reason_has_none(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        await _runs(session, 1)

        runs = (await _page(client, token))["runs"]

        assert isinstance(runs, list)
        assert runs[0]["reason"] is None
