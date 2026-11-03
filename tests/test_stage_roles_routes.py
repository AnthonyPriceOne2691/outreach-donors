"""Роли этапов на настоящих маршрутах: чужой этап закрывает сервер, а не меню.

Слово Anthony 02.11.2026: аутрич и рекламодатели — разные роли, обе отдельно от админской, и скрыто
должно быть не только меню. Меню защищает только от случайного нажатия: прямой запрос учётки видит
лишь сервер. Поэтому проверка идёт по схеме настоящего приложения, маршрут за маршрутом, и любой новый
маршрут обязан быть здесь назван: без этого он молча остался бы открыт обеим ролям.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.api.watchdog import routes as watchdog_routes
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.ops import silence
from backend.features.ops.alarms import Alarm
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_overview import _run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
Route = tuple[str, str]

#: Этап 1 — начала путей и отдельные маршруты общих роутеров.
STAGE_ONE_PATHS = (
    "/api/runs",
    "/api/keywords",
    "/api/selection",
    "/api/review",
    "/api/price-rechecks",
    "/api/contacts",
    "/api/settings/thresholds",
    "/api/settings/preview",
    "/api/replies/calibration",
)
STAGE_ONE_ROUTES: frozenset[Route] = frozenset(
    {
        ("GET", "/api/donors"),
        ("GET", "/api/donors/export"),
        ("POST", "/api/donors/export"),
        ("GET", "/api/donors/{donor_id}"),
        ("POST", "/api/donors/{donor_id}/price"),
    }
)
#: Этап 2: обход и доноры в обход — его вход, а не Этап 1.
STAGE_TWO_PATHS = (
    "/api/advertisers",
    "/api/crawls",
    "/api/donors/import",
    "/api/settings/advertisers",
    "/api/replies/leads.csv",
)
STAGE_TWO_ROUTES: frozenset[Route] = frozenset({("POST", "/api/donors")})
#: «Расход»: экран и общий кап Ahrefs.
SPENDING_ROUTES: frozenset[Route] = frozenset(
    {("GET", "/api/usage"), ("POST", "/api/usage/ahrefs-cap")}
)
#: Общее: фильтр этапа внутри маршрута, своё право или вход извне. Проверяется своими тестами.
SHARED_PATHS = (
    "/api/agent",
    "/api/auth",
    "/api/events",
    "/api/health",
    "/api/inbound",
    "/api/jobs",
    "/api/journal",
    "/api/letters",
    "/api/messages",
    "/api/overview",
    "/api/replies",
    "/api/sales",
    "/api/senders",
    "/api/suppressions",
    "/api/threads",
    "/api/unsubscribe",
    "/api/usage/hunter-cap",
    "/api/usage/limits",
    "/api/usage/llm-caps",
    "/api/users",
    "/api/watchdog",
)


def _under(path: str, starts: tuple[str, ...]) -> bool:
    return any(path == start or path.startswith(f"{start}/") for start in starts)


def _kind(route: Route) -> str | None:
    """Чей маршрут: Этап 2 и точные маршруты — раньше общих начал `/api/donors` и `/api/replies`."""
    _, path = route
    if _under(path, STAGE_TWO_PATHS) or route in STAGE_TWO_ROUTES:
        return "two"
    if _under(path, STAGE_ONE_PATHS) or route in STAGE_ONE_ROUTES:
        return "one"
    if route in SPENDING_ROUTES:
        return "usage"
    return "shared" if _under(path, SHARED_PATHS) else None


def _routes(app: FastAPI) -> list[Route]:
    """Маршруты из схемы: с FastAPI 0.141 включённые роутеры в `app.routes` ленивы."""
    return sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    )


def _of(app: FastAPI, kind: str) -> list[Route]:
    return [route for route in _routes(app) if _kind(route) == kind]


def _filled(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
async def outreach(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("outreach@team.example.test", role=UserRole.DONORS)
    return bearer(await sign_in("outreach@team.example.test"))


@pytest.fixture
async def advertisers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("stage-two@team.example.test", role=UserRole.ADVERTISERS)
    return bearer(await sign_in("stage-two@team.example.test"))


async def _refused(
    client: AsyncClient, routes: list[Route], headers: dict[str, str], words: str
) -> list[str]:
    """Маршруты, ответившие не отказом права: пусто — закрыты все."""
    assert routes
    wrong = []
    for method, path in routes:
        answer = await client.request(method, _filled(path), headers=headers)
        if answer.status_code != 403 or answer.json().get("detail") != words:
            wrong.append(f"{method} {path}: {answer.status_code} {answer.text[:80]}")
    return wrong


async def _open(client: AsyncClient, routes: list[Route], headers: dict[str, str]) -> list[str]:
    """Чтение своего этапа не упирается в право: 404 и 422 тут законны, 403 — нет."""
    reads = [path for method, path in routes if method == "GET"]
    assert reads
    wrong = []
    for path in reads:
        answer = await client.get(_filled(path), headers=headers)
        if answer.status_code == 403:
            wrong.append(f"GET {path}: {answer.text[:80]}")
    return wrong


def test_every_route_is_named_by_its_stage(api_app: FastAPI) -> None:
    """Новый маршрут не остаётся открытым обеим ролям молча: сперва его называют здесь."""
    assert [route for route in _routes(api_app) if _kind(route) is None] == []


async def test_stage_one_is_closed_to_the_advertisers_role(
    client: AsyncClient, api_app: FastAPI, advertisers: dict[str, str]
) -> None:
    words = no_right(Permission.DONORS)
    assert await _refused(client, _of(api_app, "one"), advertisers, words) == []


async def test_stage_two_is_closed_to_the_outreach_role(
    client: AsyncClient, api_app: FastAPI, outreach: dict[str, str]
) -> None:
    words = no_right(Permission.ADVERTISERS)
    assert await _refused(client, _of(api_app, "two"), outreach, words) == []


async def test_spending_is_closed_to_both_stage_roles(
    client: AsyncClient,
    api_app: FastAPI,
    outreach: dict[str, str],
    advertisers: dict[str, str],
) -> None:
    """Кап Ahrefs общий на этапы: право «править пороги» роли этапа его не открывает."""
    words = no_right(Permission.USAGE)
    for headers in (outreach, advertisers):
        assert await _refused(client, _of(api_app, "usage"), headers, words) == []


async def test_the_outreach_role_reads_its_stage(
    client: AsyncClient, api_app: FastAPI, outreach: dict[str, str]
) -> None:
    assert await _open(client, _of(api_app, "one"), outreach) == []


async def test_the_advertisers_role_reads_its_stage(
    client: AsyncClient, api_app: FastAPI, advertisers: dict[str, str]
) -> None:
    assert await _open(client, _of(api_app, "two"), advertisers) == []


async def test_the_operator_lost_nothing(
    client: AsyncClient, api_app: FastAPI, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user("operator@team.example.test", role=UserRole.OPERATOR)
    headers = bearer(await sign_in("operator@team.example.test"))

    assert await _open(client, [*_of(api_app, "one"), *_of(api_app, "two")], headers) == []


class TestSharedScreens:
    """Общие экраны открывают роли её этап, а чужой называют словами права."""

    async def test_the_letters_screen_refuses_the_default_stage_in_words(
        self, client: AsyncClient, advertisers: dict[str, str]
    ) -> None:
        refused = await client.get("/api/letters", headers=advertisers)
        own = await client.get("/api/letters?stage=advertisers", headers=advertisers)

        assert refused.status_code == 403
        assert refused.json()["detail"] == "Письма доноров — только с правом «Доноры»"
        assert own.status_code == 200, own.text

    async def test_the_agent_shows_only_the_stage_of_the_role(
        self, client: AsyncClient, advertisers: dict[str, str], outreach: dict[str, str]
    ) -> None:
        for headers, stage in ((advertisers, "advertisers"), (outreach, "donors")):
            answer = await client.get("/api/agent/settings", headers=headers)

            assert answer.status_code == 200, answer.text
            assert [one["stage"] for one in answer.json()["stages"]] == [stage]

    async def test_the_neighbour_queue_of_forms_is_not_counted_without_its_right(
        self, client: AsyncClient, advertisers: dict[str, str]
    ) -> None:
        answer = await client.get("/api/advertisers/forms", headers=advertisers)

        assert answer.status_code == 200, answer.text
        assert answer.json()["donors_waiting"] == 0

    async def test_the_overview_has_no_numbers_of_stage_one(
        self, client: AsyncClient, session: AsyncSession, advertisers: dict[str, str]
    ) -> None:
        await _run(session)
        await session.commit()

        answer = await client.get("/api/overview", headers=advertisers)

        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert set(body["letters"]) == {"advertisers"}
        assert body["last_run"] is None
        assert body["donors"]["total"] == 0
        assert body["waiting"]["review"] == 0
        assert body["ahrefs_cap"] is None

    async def test_the_overview_of_the_outreach_role_keeps_its_run(
        self, client: AsyncClient, session: AsyncSession, outreach: dict[str, str]
    ) -> None:
        await _run(session)
        await session.commit()

        answer = await client.get("/api/overview", headers=outreach)

        assert answer.status_code == 200, answer.text
        assert set(answer.json()["letters"]) == {"donors"}
        assert answer.json()["last_run"] is not None


class TestServiceAlarms:
    """Ключи, балансы и опрос провайдеров — служебное: роль этапа их не видит и не опрашивает."""

    @pytest.fixture
    def found(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Две тревоги — служебная и этапа; в списке — кого спросили об опросе."""
        asked: list[str] = []

        async def alarms(*_args: object, **_kwargs: object) -> list[Alarm]:
            return [
                Alarm(code="key", title="Ключ", detail="Слова"),
                Alarm(code="stage", title="Этап", detail="Слова", stage=Stage.DONORS),
            ]

        async def probe() -> None:
            asked.append("опрос")

        monkeypatch.setattr(watchdog_routes, "alarms", alarms)
        monkeypatch.setattr(watchdog_routes, "probe_providers", probe)
        return asked

    async def test_the_stage_role_sees_only_the_alarms_of_a_stage(
        self, client: AsyncClient, outreach: dict[str, str], found: list[str]
    ) -> None:
        answer = await client.get("/api/watchdog", headers=outreach)

        assert [one["code"] for one in answer.json()["alarms"]] == ["stage"]
        assert found == []

    async def test_the_other_stage_role_does_not_see_that_alarm(
        self, client: AsyncClient, advertisers: dict[str, str], found: list[str]
    ) -> None:
        answer = await client.get("/api/watchdog", headers=advertisers)

        assert answer.json()["alarms"] == []
        assert found == []

    async def test_the_operator_sees_the_service_alarms_as_before(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        found: list[str],
    ) -> None:
        await make_user("operator@team.example.test", role=UserRole.OPERATOR)
        headers = bearer(await sign_in("operator@team.example.test"))

        answer = await client.get("/api/watchdog", headers=headers)

        assert [one["code"] for one in answer.json()["alarms"]] == ["key", "stage"]
        assert found == ["опрос"]

    async def test_a_stuck_run_is_an_alarm_of_stage_one(self, session: AsyncSession) -> None:
        """Прогоны — работа Этапа 1: роль «Аутрич» не теряет эту тревогу вместе со служебными."""
        run = await _run(session)
        run.status = RunStatus.RUNNING
        await session.flush()

        alarm = await silence._runs_stuck(session, datetime.now(UTC) + timedelta(days=1))

        assert alarm is not None
        assert alarm.stage is Stage.DONORS
