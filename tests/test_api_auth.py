"""Вход по HTTP: пропуск, «кто я», смена своего пароля.

Проверяется то, что ломается тихо. Отказ входа обязан быть одинаковым
на «нет такой почты» и «неверный пароль» — разные тексты превращают
перебор в точечный. Неудачная попытка обязана остаться в журнале —
иначе подбор выглядит как тишина. Разовый пароль обязан закрывать
работу до смены — иначе требование сменить его держится на вежливости
интерфейса.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from backend.api.app import create_app
from backend.config.startup_checks import ConfigError
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]


async def _actions(session: AsyncSession) -> list[AuditAction]:
    rows = await session.execute(select(AuditLogModel.action))
    return list(rows.scalars().all())


class TestLogin:
    async def test_login_gives_token_and_card(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user("ivan@site.com", role=UserRole.OPERATOR)

        response = await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "пароль-для-теста"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token"]
        assert body["user"]["email"] == "ivan@site.com"
        assert body["user"]["role"] == "operator"
        # Права приходят списком действий: интерфейс не повторяет у себя
        # матрицу прав и не разъезжается с ней.
        assert body["user"]["permissions"] == ["run", "settings", "view"]

    async def test_email_case_and_spaces_do_not_matter(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user("ivan@site.com")
        response = await client.post(
            "/api/auth/login", json={"email": "  Ivan@Site.com ", "password": "пароль-для-теста"}
        )
        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("ivan@site.com", "не тот пароль"),
            ("никого@site.com", "пароль-для-теста"),
        ],
        ids=["неверный пароль", "нет такой почты"],
    )
    async def test_refusal_is_the_same_for_every_reason(
        self, client: AsyncClient, make_user: MakeUser, email: str, password: str
    ) -> None:
        await make_user("ivan@site.com")

        response = await client.post("/api/auth/login", json={"email": email, "password": password})

        assert response.status_code == 401
        assert response.json()["detail"] == "Неверная почта или пароль"

    async def test_disabled_user_does_not_get_in(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user("уволен@site.com", is_active=False)

        response = await client.post(
            "/api/auth/login", json={"email": "уволен@site.com", "password": "пароль-для-теста"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Неверная почта или пароль"

    async def test_failed_attempt_survives_the_refusal(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Главная проверка файла: запись о неудаче делается в той же
        транзакции, которую отказ откатил бы вместе с собой."""
        await make_user("ivan@site.com")

        await client.post("/api/auth/login", json={"email": "ivan@site.com", "password": "мимо"})

        assert AuditAction.LOGIN_FAILED in await _actions(session)

    async def test_successful_login_is_written_down(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        await make_user("ivan@site.com")
        await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "пароль-для-теста"}
        )
        assert AuditAction.LOGIN in await _actions(session)


class TestBruteForce:
    async def test_attempts_run_out(self, client: AsyncClient, make_user: MakeUser) -> None:
        await make_user("ivan@site.com")
        payload = {"email": "ivan@site.com", "password": "мимо"}

        codes = [(await client.post("/api/auth/login", json=payload)).status_code for _ in range(6)]

        assert codes[:5] == [401] * 5
        assert codes[5] == 429

    async def test_refusal_says_how_long_to_wait(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user("ivan@site.com")
        payload = {"email": "ivan@site.com", "password": "мимо"}
        for _ in range(5):
            await client.post("/api/auth/login", json=payload)

        response = await client.post("/api/auth/login", json=payload)

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    async def test_successful_login_clears_the_count(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        """Человек, который ошибся раскладкой и потом вошёл, не должен
        упираться в предел на следующей ошибке."""
        await make_user("ivan@site.com")
        for _ in range(4):
            await client.post(
                "/api/auth/login", json={"email": "ivan@site.com", "password": "мимо"}
            )

        await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "пароль-для-теста"}
        )
        again = await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "мимо"}
        )

        assert again.status_code == 401


class TestWhoAmI:
    async def test_me_returns_the_card(
        self, client: AsyncClient, make_user: MakeUser, sign_in: Callable[..., Awaitable[str]]
    ) -> None:
        await make_user("ivan@site.com", permissions={"send": True})
        token = await sign_in("ivan@site.com")

        response = await client.get("/api/auth/me", headers=bearer(token))

        assert response.status_code == 200, response.text
        assert response.json()["email"] == "ivan@site.com"
        # Точечное право видно в списке: оператору отправка не положена ролью.
        assert "send" in response.json()["permissions"]

    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic ivan:secret"}],
        ids=["без пропуска", "подделка", "не тот заголовок"],
    )
    async def test_no_pass_no_answer(self, client: AsyncClient, headers: dict[str, str]) -> None:
        response = await client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    async def test_disabled_after_issue_stops_working(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        """Пропуск живёт сутки, а увольнение случается сегодня: права
        берутся из базы на каждом запросе, а не из пропуска."""
        user = await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")

        await AccessRepository(session).update_access(user.id, is_active=False)
        await session.commit()

        response = await client.get("/api/auth/me", headers=bearer(token))
        assert response.status_code == 401


class TestOwnPassword:
    async def test_change_works_and_old_password_stops(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")

        changed = await client.post(
            "/api/auth/password",
            headers=bearer(token),
            json={"current": "пароль-для-теста", "new": "три слова подряд длиннее"},
        )

        assert changed.status_code == 204, changed.text
        old = await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "пароль-для-теста"}
        )
        assert old.status_code == 401
        await sign_in("ivan@site.com", "три слова подряд длиннее")

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"current": "мимо", "new": "три слова подряд длиннее"}, "Старый пароль не подошёл"),
            ({"current": "пароль-для-теста", "new": "коротко"}, "Длина надёжнее сложности"),
            ({"current": "пароль-для-теста", "new": "пароль-для-теста"}, "совпадает со старым"),
        ],
        ids=["старый не тот", "новый короткий", "новый тот же"],
    )
    async def test_refusals_say_what_to_do(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
        body: dict[str, str],
        expected: str,
    ) -> None:
        await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")

        response = await client.post("/api/auth/password", headers=bearer(token), json=body)

        assert response.status_code == 400
        assert expected in response.json()["detail"]


class TestOneTimePasswordGate:
    """Разовый пароль закрывает работу до смены — иначе выданный голосом
    пароль остаётся действующим месяцами."""

    async def test_work_is_closed_until_password_changed(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("новый@site.com", role=UserRole.ADMIN, must_change_password=True)
        token = await sign_in("новый@site.com")

        response = await client.get("/api/users", headers=bearer(token))

        assert response.status_code == 403
        assert "смените разовый пароль" in response.json()["detail"]

    async def test_me_and_password_stay_open(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("новый@site.com", must_change_password=True)
        token = await sign_in("новый@site.com")

        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200
        changed = await client.post(
            "/api/auth/password",
            headers=bearer(token),
            json={"current": "пароль-для-теста", "new": "три слова подряд длиннее"},
        )
        assert changed.status_code == 204, changed.text

    async def test_change_opens_the_work(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("новый@site.com", role=UserRole.ADMIN, must_change_password=True)
        token = await sign_in("новый@site.com")
        await client.post(
            "/api/auth/password",
            headers=bearer(token),
            json={"current": "пароль-для-теста", "new": "три слова подряд длиннее"},
        )

        assert (await client.get("/api/users", headers=bearer(token))).status_code == 200
        assert (await client.get("/api/auth/me", headers=bearer(token))).json()[
            "must_change_password"
        ] is False


class TestStartup:
    def test_app_refuses_to_start_without_a_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сервис без секрета подписи не пускает никого. Узнать об этом
        надо при развёртывании, а не от сотрудника, который не может войти."""
        monkeypatch.setattr("backend.config.access.JWT_SECRET", "")

        with pytest.raises(ConfigError, match="ACCESS_JWT_SECRET"):
            create_app()


class TestBlockedAttemptsAreVisible:
    async def test_stopped_brute_force_is_logged(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Найдено живым прогоном: остановленная счётчиком попытка в журнал
        не попадает, и подбор выглядит прекратившимся ровно в тот момент,
        когда он продолжается. Значит, он обязан быть виден хотя бы в логе."""
        await make_user("ivan@site.com")
        payload = {"email": "ivan@site.com", "password": "мимо"}
        for _ in range(5):
            await client.post("/api/auth/login", json=payload)

        with caplog.at_level(logging.WARNING):
            blocked = await client.post("/api/auth/login", json=payload)

        assert blocked.status_code == 429
        assert any("попытки исчерпаны" in record.message for record in caplog.records)
