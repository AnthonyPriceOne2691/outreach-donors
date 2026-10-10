"""Прогон и доноры по HTTP: права, смета и таблица.

Права проверяются таблицей, как и в остальных разделах. Отдельно
проверяется главное свойство экрана прогона: смета ничего не тратит,
а запуск кладёт задачу в очередь, а не выполняет её в запросе.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core import usage
from backend.features.core.domain import ContactSource, DonorStatus, RunStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.run import RunModel, RunSettingsModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import ThresholdsRepository, defaults, thresholds_of
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)

RUN_BODY = {"keywords": ["ремонт квартир"], "country": "us", "depth_pages": 1}

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/runs/countries", None, "run"),
    ("POST", "/api/runs/estimate", RUN_BODY, "run"),
    ("POST", "/api/runs", RUN_BODY, "run"),
    ("GET", "/api/runs", None, "view"),
    ("GET", "/api/runs/with-accepted", None, "view"),
    ("GET", "/api/runs/{run}", None, "view"),
    ("GET", "/api/donors", None, "view"),
    ("GET", "/api/donors/{donor}", None, "view"),
    ("GET", "/api/donors/export", None, "view"),
    ("POST", "/api/donors/export", {"ids": [1]}, "view"),
    # Цена руками — то же право, что у адреса руками: пишет в базу доноров.
    ("POST", "/api/donors", {"host": "example.com", "price": "150"}, "run"),
    ("POST", "/api/donors/{donor}/price", {"price": "150"}, "run"),
]


@pytest.fixture
async def donors(session: AsyncSession) -> list[DonorModel]:
    """Три донора — принятые человеком: список показывает только их
    (решение 26.09.2026, `donors/standing.py`)."""
    made: list[DonorModel] = []
    for index, (host, status, dr, reason) in enumerate(
        [
            ("good.example.test", DonorStatus.SUITABLE, 45, None),
            ("weak.example.test", DonorStatus.UNSUITABLE, 8, "dr ниже порога"),
            ("unknown.example.test", DonorStatus.UNCHECKED, None, None),
        ]
    ):
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        donor = DonorModel(
            domain_id=domain.id,
            status=status,
            dr=dr,
            reject_reason=reason,
            org_traffic=1000 * (index + 1),
            metrics_refreshed_at=NOW - timedelta(days=index),
            review="accepted",
        )
        session.add(donor)
        if index == 0:
            session.add(
                ContactModel(
                    domain_id=domain.id,
                    email=f"editor@{host}",
                    source=ContactSource.PAGE,
                )
            )
        made.append(donor)
    await session.commit()
    return made


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    """Оператор без права на прогон: смотреть базу может, тратить — нет."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"run": False})
    return await sign_in("зритель@site.com")


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        donors: list[DonorModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        target = path.format(run=1, donor=donors[0].id)
        response = await client.request(method, target, json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_run_needs_its_own_right(
        self,
        client: AsyncClient,
        viewer_token: str,
        donors: list[DonorModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Смотреть базу и тратить юниты — разные права. Отобранное
        точечно право на прогон закрывает смету и запуск, но не таблицу."""
        target = path.format(run=1, donor=donors[0].id)
        response = await client.request(method, target, json=body, headers=bearer(viewer_token))

        if permission == "run":
            assert response.status_code == 403
            assert "«run»" in response.json()["detail"]
        else:
            assert response.status_code in (200, 404), response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith(("/api/runs", "/api/donors"))
            for method in methods
        }
        in_table = {
            (method, path.replace("{run}", "{run_id}").replace("{donor}", "{donor_id}"))
            for method, path, _, _ in ROUTES
        }
        assert in_app == in_table


class TestDonorsTable:
    async def test_counts_separate_unchecked_from_unsuitable(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        """«Не проверен» — не «не подходит»: спутать их значит копить
        ложные отказы и терять доноров."""
        response = await client.get("/api/donors", headers=bearer(operator_token))

        counts = response.json()["counts"]
        assert counts["suitable"] == 1
        assert counts["unsuitable"] == 1
        assert counts["unchecked"] == 1

    async def test_reject_reason_is_always_returned(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get("/api/donors?status=unsuitable", headers=bearer(operator_token))

        row = response.json()["rows"][0]
        assert row["reject_reason"] == "dr ниже порога"

    async def test_search_looks_at_host_and_reason(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        by_reason = await client.get("/api/donors?search=порога", headers=bearer(operator_token))

        assert [row["host"] for row in by_reason.json()["rows"]] == ["weak.example.test"]

    async def test_total_is_counted_before_the_page(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        """Без общего числа «ничего не найдено» читается как «база пуста»."""
        response = await client.get("/api/donors?limit=1", headers=bearer(operator_token))

        body = response.json()
        assert len(body["rows"]) == 1
        assert body["total"] == 3

    async def test_filter_by_contact(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get("/api/donors?has_contact=true", headers=bearer(operator_token))
        # «С адресом» — адрес есть в базе, а не отметка исхода поиска на доноре
        # (проверка прода 10.10.2026): у good.example.test адрес есть, исхода нет.
        assert [row["host"] for row in response.json()["rows"]] == ["good.example.test"]

    async def test_card_shows_contacts_and_expiry(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get(f"/api/donors/{donors[0].id}", headers=bearer(operator_token))

        card = response.json()
        assert card["contacts"][0]["email"].startswith("editor@")
        assert card["expires_at"] is not None
        assert card["fresh"] is True

    async def test_card_lists_every_price_of_the_last_reply(
        self,
        client: AsyncClient,
        operator_token: str,
        donors: list[DonorModel],
        session: AsyncSession,
    ) -> None:
        """Рядом с последней ценой — все цены того же ответа; записанная до
        списка цена отдаёт пусто, а не «цен нет»."""
        donors[0].last_offers = [
            {
                "product": "link insertion",
                "niche": None,
                "price": "80",
                "currency": "USD",
                "period": None,
            }
        ]
        await session.commit()

        listed = await client.get(f"/api/donors/{donors[0].id}", headers=bearer(operator_token))
        unknown = await client.get(f"/api/donors/{donors[1].id}", headers=bearer(operator_token))

        assert listed.json()["last_offers"] == [
            {
                "product": "link insertion",
                "niche": None,
                "price": "80",
                "currency": "USD",
                "period": None,
            }
        ]
        assert unknown.json()["last_offers"] is None

    async def test_unknown_donor_is_not_found(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        response = await client.get("/api/donors/9999", headers=bearer(operator_token))
        assert response.status_code == 404


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class FakeQueue:
    """Очередь, которая ничего не выполняет: проверяется, что в неё
    положили, а не что rq работает."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, path: str, *args: Any, **_: Any) -> FakeJob:
        self.calls.append((path, args))
        return FakeJob("job-из-теста")


async def _cap_of_run(session: AsyncSession, run_id: int) -> int:
    """Потолок, с которым прогон уедет в задачу: он лежит в настройках,
    и берёт его оттуда сам воркер."""
    run = await RunRepository(session).get(run_id)
    settings = await session.get(RunSettingsModel, run.settings_id)
    assert settings is not None
    return settings.units_cap


class TestTheCeilings:
    """Потолки, которые до этого среза были объявлены и не применялись."""

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def test_more_keywords_than_allowed_is_refused(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        """Настройка «до ста ключей за прогон» существовала и не
        проверялась нигде: маршрут принимал впятеро больше."""
        response = await client.post(
            "/api/runs",
            json={"keywords": [f"ключ {n}" for n in range(101)], "country": "us"},
            headers=bearer(operator_token),
        )

        assert response.status_code == 422
        assert "не больше 100" in response.text

    async def test_own_ceiling_reaches_the_run(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """Поле «потолок юнитов» — способ попробовать нишу дёшево,
        не сокращая список ключей."""
        started = await client.post(
            "/api/runs",
            json={**RUN_BODY, "cap": 5_000},
            headers=bearer(operator_token),
        )

        assert started.status_code == 200, started.text
        cap = await _cap_of_run(session, started.json()["run_id"])
        assert cap == 5_000

    async def test_spent_units_lower_the_ceiling(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """Кап месячный: потраченное вычитается, иначе он ограничивает
        один прогон, а на экране расхода называется месячным."""
        usage.record(session, operation="batch_metrics", units=99_000)
        await session.commit()

        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        cap = await _cap_of_run(session, started.json()["run_id"])
        assert cap == 1_000


class TestStartPutsTheRunOnTheScreen:
    """Прогон существует с нажатия, а не с первой траты.

    До этого строка появлялась внутри задачи, уже после выдачи: всё это
    время экран показывал пустоту, а если задачу никто не брал — всегда.
    """

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        # Настройки задаются тестом, а не берутся из окружения машины.
        # Первая версия проходила локально и падала в CI: ключ Ahrefs
        # лежал в `.env` разработчика, и проверка настроек на маршруте
        # пропускала запуск по чужой причине.
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def test_row_appears_queued_with_its_job(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue
    ) -> None:
        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        assert queue.calls == [("backend.workers.jobs.run_donor_search", (run_id,))]

        listed = await client.get("/api/runs", headers=bearer(operator_token))
        card = next(row for row in listed.json()["runs"] if row["id"] == run_id)
        assert card["status"] == "queued"
        assert card["estimated_units"] is None
        assert card["hosts"] is None

    async def test_job_gets_only_the_run_number(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        """Доводы задачи — один номер. Ключи и глубина лежат в строке:
        продолжению после смерти воркера неоткуда взять другие."""
        await client.post(
            "/api/runs",
            json={"keywords": ["ремонт", "кухня"], "country": "de", "depth_pages": 3},
            headers=bearer(operator_token),
        )

        _, args = queue.calls[0]
        assert len(args) == 1

        run = (
            (await session.execute(select(RunModel).order_by(RunModel.id.desc()))).scalars().first()
        )
        assert run is not None
        assert run.depth_pages == 3
        assert run.country == "de"
        assert run.job_id == "job-из-теста"

    async def test_screen_knows_there_is_nobody_to_take_the_job(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Число живых воркеров — единственное, что отличает работающий
        сервис от очереди, из которой никто не читает."""
        monkeypatch.setattr("backend.api.runs.routes.workers_alive", lambda: 0)

        listed = await client.get("/api/runs", headers=bearer(operator_token))

        assert listed.json()["workers"] == 0

    async def test_unknown_worker_count_is_not_zero(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis не ответил — это «не знаю», а не «никого нет»."""
        monkeypatch.setattr("backend.api.runs.routes.workers_alive", lambda: None)

        listed = await client.get("/api/runs", headers=bearer(operator_token))

        assert listed.json()["workers"] is None


class TestNoSecondSameRun:
    """Такой же прогон, пока первый не закончен, — 409 словами (аудит 10.10.2026, №2).

    Двойной щелчок или двое людей ставили два одинаковых прогона: единственный воркер шёл
    ими по очереди, и второй покупал ту же выдачу ещё раз. Гонка двух нажатий разом —
    `tests/test_run_start_race.py`.
    """

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def _start(self, client: AsyncClient, token: str, **changes: Any) -> httpx.Response:
        body = {"keywords": ["ремонт квартир", "дизайн"], "country": "us", **changes}
        return await client.post("/api/runs", json=body, headers=bearer(token))

    async def test_the_same_run_twice_is_refused_in_words(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue
    ) -> None:
        first = await self._start(client, operator_token)
        second = await self._start(
            client, operator_token, keywords=["Дизайн ", "ремонт квартир", "дизайн"], country="US"
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
        run_id = first.json()["run_id"]
        assert second.json()["detail"].startswith(f"Такой же прогон №{run_id} ещё не закончен")
        assert len(queue.calls) == 1, "второй — до очереди"

    @pytest.mark.parametrize(
        "changes",
        [{"keywords": ["ремонт квартир"]}, {"country": "de"}, {"depth_pages": 2}],
        ids=["другие ключи", "другая страна", "другая глубина"],
    )
    async def test_another_run_is_not_a_duplicate(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        changes: dict[str, Any],
    ) -> None:
        await self._start(client, operator_token)

        another = await self._start(client, operator_token, **changes)

        assert another.status_code == 200, another.text

    async def test_once_the_first_is_over_the_same_run_is_welcome(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        first = await self._start(client, operator_token)
        run = await RunRepository(session).get(first.json()["run_id"])
        run.status = RunStatus.DONE
        await session.commit()

        again = await self._start(client, operator_token)

        assert again.status_code == 200, again.text


async def _settings_of_run(session: AsyncSession, run_id: int) -> RunSettingsModel:
    run = await RunRepository(session).get(run_id)
    settings = await session.get(RunSettingsModel, run.settings_id)
    assert settings is not None
    return settings


class TestThresholdsOfTheScreen:
    """Прогон берёт пороги с экрана «Пороги», а не умолчания конфига (аудит 10.10.2026).

    Запуск заводил настройки прогона из `defaults()`, и задача передавала сбору их же:
    человек правил пороги на экране, а прогоны отсеивали по конфигу. Хуже того, строка
    настроек прогона сама становилась «текущей версией» — экран после каждого запуска
    показывал умолчания вместо сохранённого.
    """

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def test_saved_version_is_what_the_run_uses(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        await ThresholdsRepository(session).save(
            Thresholds(min_dr=77, min_org_traffic=7_007, min_refdomains=707, min_keywords=770),
            author="оператор@site.com",
        )
        await session.commit()

        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert started.status_code == 200, started.text
        settings = await _settings_of_run(session, started.json()["run_id"])
        assert thresholds_of(settings) == Thresholds(77, 7_007, 707, 770)
        current = await ThresholdsRepository(session).current()
        assert current is not None
        assert thresholds_of(current) == Thresholds(77, 7_007, 707, 770), (
            "запуск не откатывает экран «Пороги» к умолчаниям"
        )

    async def test_without_a_saved_version_the_config_stands(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert started.status_code == 200, started.text
        settings = await _settings_of_run(session, started.json()["run_id"])
        assert thresholds_of(settings) == defaults()


def _ahrefs_answers(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Остаток у Ahrefs отвечает этим кодом — настоящий клиент, подменён только транспорт."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, text="unauthorized" if code == 401 else "")

    monkeypatch.setattr(
        "backend.api.runs.routes.AhrefsClient",
        lambda: AhrefsClient(
            api_key="k",
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(answer), base_url="https://api.test"
            ),
        ),
    )


class TestRefusalsInWords:
    """Смета и запуск отказывают словами, а не «Internal Server Error» (аудит 10.10.2026).

    Остаток у Ahrefs не узнать — смета отвечала пятисоткой: так выглядел отозванный 08.10 ключ.
    Песочница выдачи или нет ключа — то же на «Запустить», и написанный человеку отказ
    («Песочница… прогон не запускает») до экрана не доходил.
    """

    async def test_revoked_key_on_the_estimate_is_a_state_in_words(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ключ отозван — повтор не поможет: 409 и причина, чинят ключ."""
        _ahrefs_answers(monkeypatch, 401)

        response = await client.post(
            "/api/runs/estimate", json=RUN_BODY, headers=bearer(operator_token)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith("Не удалось узнать остаток юнитов у Ahrefs")
        assert "401" in response.json()["detail"]

    async def test_ahrefs_down_on_the_estimate_is_worth_a_retry(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Провайдер лежит — 503: поможет повтор, а не правка."""
        _ahrefs_answers(monkeypatch, 503)

        response = await client.post(
            "/api/runs/estimate", json=RUN_BODY, headers=bearer(operator_token)
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"].startswith("Не удалось узнать остаток юнитов у Ahrefs")

    @pytest.mark.parametrize(
        ("sandbox", "key", "words"),
        [
            (True, "ключ-для-теста", "Песочница выдачи (SERP_SANDBOX=true) прогон не запускает"),
            (False, "", "AHREFS_API_KEY"),
        ],
        ids=["песочница", "нет ключа"],
    )
    async def test_settings_refuse_the_start_in_their_own_words(
        self,
        client: AsyncClient,
        operator_token: str,
        monkeypatch: pytest.MonkeyPatch,
        sandbox: bool,
        key: str,
        words: str,
    ) -> None:
        monkeypatch.setattr("backend.config.serp.SANDBOX", sandbox)
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", key)
        queue = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: queue)

        response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert response.status_code == 409, response.text
        assert words in response.json()["detail"]
        assert queue.calls == [], "отказ настроек — до очереди"
