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
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from backend.api import errors
from backend.api.app import create_app
from backend.config.startup_checks import ConfigError
from backend.features.access.login import LoginFailedError
from backend.features.access.passwords import verify_password
from backend.features.access.permissions import SignedOutError
from backend.features.access.repository import AccessRepository
from backend.features.access.tokens import TOKEN_HEADER, TokenError
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from fastapi import FastAPI, status
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
        assert body["user"]["permissions"] == ["prices", "run", "sales", "settings", "view"]

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

        assert response.status_code == 400
        assert response.json()["detail"] == "Неверная почта или пароль"

    async def test_disabled_user_does_not_get_in(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user("уволен@site.com", is_active=False)

        response = await client.post(
            "/api/auth/login", json={"email": "уволен@site.com", "password": "пароль-для-теста"}
        )

        assert response.status_code == 400
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

        assert codes[:5] == [400] * 5
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

        assert again.status_code == 400


class TestTimingDoesNotTell:
    async def test_unknown_email_costs_a_password_check_too(
        self, client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Аудит 10.10.2026, №15: bcrypt шёл только для существующей почты, и отказ по
        незнакомой приходил быстрее — время ответа называло, какие учётки есть. Теперь
        пароль сверяется и тогда: с подставным хешем той же цены."""
        await make_user("ivan@site.com")
        checked: list[str] = []

        def spy(plain: str, password_hash: str | None) -> bool:
            checked.append(password_hash or "")
            return verify_password(plain, password_hash)

        monkeypatch.setattr("backend.features.access.login.verify_password", spy)

        unknown = await client.post(
            "/api/auth/login", json={"email": "никого@site.com", "password": "мимо"}
        )
        wrong = await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "мимо"}
        )

        assert unknown.json() == wrong.json()
        assert len(checked) == 2, "пароль сверяется и с почтой, которой нет"
        unknown_hash, real_hash = checked
        assert unknown_hash.startswith("$2"), "и подставной — bcrypt"
        assert real_hash.startswith("$2")
        assert unknown_hash.split("$")[2] == real_hash.split("$")[2], "той же ценой"


WRONG_OLD = {"current": "мимо", "new": "три слова подряд длиннее"}
RIGHT_OLD = {"current": "пароль-для-теста", "new": "три слова подряд длиннее"}


class TestOwnPasswordAttempts:
    """Подбор старого пароля по пропуску — с тем же пределом, что у входа (аудит 10.10.2026).

    Украденный суточный пропуск позволял перебирать старый пароль без счёта, а с ним —
    пароль, которым человек, может быть, входит и в другие сервисы.
    """

    async def test_guessing_the_old_password_runs_out(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")

        codes = [
            (
                await client.post("/api/auth/password", headers=bearer(token), json=WRONG_OLD)
            ).status_code
            for _ in range(6)
        ]
        right = await client.post("/api/auth/password", headers=bearer(token), json=RIGHT_OLD)

        assert codes == [400] * 5 + [429]
        assert right.status_code == 429, "исчерпано — не проверяется и верный"
        assert int(right.headers["Retry-After"]) > 0
        # Пароль не сменён, и вход по нему не задет: у входа свой счёт.
        await sign_in("ivan@site.com")

    async def test_a_change_clears_the_count(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        """Ошибся раскладкой и сменил — следующая ошибка не упирается в предел."""
        await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")
        for _ in range(4):
            await client.post("/api/auth/password", headers=bearer(token), json=WRONG_OLD)

        changed = await client.post("/api/auth/password", headers=bearer(token), json=RIGHT_OLD)
        assert changed.status_code == 200, changed.text
        # Дальше — новым пропуском: прежний смена пароля закрыла.
        renewed = changed.json()["token"]
        again = [
            (
                await client.post(
                    "/api/auth/password",
                    headers=bearer(renewed),
                    json={"current": "мимо", "new": "четыре слова подряд длиннее"},
                )
            ).status_code
            for _ in range(4)
        ]

        assert again == [400] * 4


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
        [
            {},
            {"Authorization": "Bearer not-a-token"},
            {TOKEN_HEADER: "not-a-token"},
            {"Authorization": "Basic ivan:secret"},
        ],
        ids=["без пропуска", "подделка", "подделка своим заголовком", "только пароль прокси"],
    )
    async def test_no_pass_no_answer(self, client: AsyncClient, headers: dict[str, str]) -> None:
        response = await client.get("/api/auth/me", headers=headers)
        assert response.status_code == 403
        assert response.headers["X-Access-Refusal"] == "signed-out"

    async def test_pass_comes_in_its_own_header_next_to_the_proxy_password(
        self, client: AsyncClient, make_user: MakeUser, sign_in: Callable[..., Awaitable[str]]
    ) -> None:
        """Экран шлёт пропуск заголовком `X-Access-Token`: `Authorization` на /api/ несёт
        общий пароль прокси, браузер шлёт его сам (вариант «А», 11.10.2026)."""
        await make_user("ivan@site.com")
        token = await sign_in("ivan@site.com")

        response = await client.get(
            "/api/auth/me", headers={TOKEN_HEADER: token, "Authorization": "Basic dGVhbTpzZWNyZXQ="}
        )

        assert response.status_code == 200, response.text
        assert response.json()["email"] == "ivan@site.com"

    async def test_without_a_pass_the_refusal_names_the_header(self, client: AsyncClient) -> None:
        response = await client.get("/api/auth/me")

        assert response.status_code == 403
        assert TOKEN_HEADER in response.json()["detail"]

    def test_the_service_never_answers_401(self) -> None:
        """401 отвечает только прокси: на 401 Chrome стирает пароль прокси, который сам
        приложил к запросу, и посреди работы всплывает окно пароля сервера (11.10.2026).
        «Входа нет» — 403 с признаком `signed-out`, неверный пароль на входе — 400."""
        assert status.HTTP_401_UNAUTHORIZED not in errors.STATUSES.values()
        assert errors.STATUSES[LoginFailedError] == status.HTTP_400_BAD_REQUEST
        assert issubclass(TokenError, SignedOutError)

    async def test_no_route_answers_401_without_a_pass(
        self, client: AsyncClient, api_app: FastAPI
    ) -> None:
        """Таблица отказов — не всё: прямой HTTPException(401) или схема входа с
        `auto_error=True` её обойдут (ревью продаж #319). Обход всех маршрутов схемы API
        без пропуска: 401 не отвечает ни один, в том числе вебхуки."""
        answered: dict[str, int] = {}
        for path, methods in api_app.openapi()["paths"].items():
            url = re.sub(r"\{[^}]+\}", "1", path)
            for method in methods:
                body = None if method == "get" else {}
                response = await client.request(method.upper(), url, json=body)
                answered[f"{method.upper()} {path}"] = response.status_code

        assert len(answered) > 50, "обход прошёл мимо маршрутов"
        assert [route for route, code in answered.items() if code == 401] == []

    def test_the_screen_sends_the_pass_in_the_same_header(self) -> None:
        """Имя заголовка — одно у сервера и экрана: разъедутся — экран останется без входа."""
        client_ts = (
            Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "client.ts"
        )
        found = re.search(
            r"export const PASS_HEADER = '([^']+)';", client_ts.read_text(encoding="utf-8")
        )

        assert found is not None, "в frontend/src/api/client.ts нет PASS_HEADER"
        assert found.group(1) == TOKEN_HEADER

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
        assert response.status_code == 403


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

        assert changed.status_code == 200, changed.text
        old = await client.post(
            "/api/auth/login", json={"email": "ivan@site.com", "password": "пароль-для-теста"}
        )
        assert old.status_code == 400
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

    async def test_refusal_is_in_words_and_marked_for_the_screen(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        """Проверка прода 10.10.2026: после сброса открытый сеанс показывал «Сначала смените
        разовый пароль: POST /api/auth/password» — метод и путь, и никуда не вёл. Теперь —
        словами, а куда вести, экран узнаёт по признаку отказа, не разбирая текст."""
        await make_user("новый@site.com", role=UserRole.ADMIN, must_change_password=True)
        token = await sign_in("новый@site.com")

        response = await client.get("/api/users", headers=bearer(token))

        assert response.status_code == 403
        said = response.json()["detail"]
        assert said == "Сначала смените разовый пароль: пока он не сменён, остальное закрыто"
        assert response.headers["X-Access-Refusal"] == "change-password"

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
        assert changed.status_code == 200, changed.text

    async def test_change_opens_the_work(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("новый@site.com", role=UserRole.ADMIN, must_change_password=True)
        token = await sign_in("новый@site.com")
        changed = await client.post(
            "/api/auth/password",
            headers=bearer(token),
            json={"current": "пароль-для-теста", "new": "три слова подряд длиннее"},
        )

        # Новый пропуск приходит ответом смены, с карточкой — как у входа.
        assert changed.json()["user"]["must_change_password"] is False
        renewed = changed.json()["token"]
        assert (await client.get("/api/users", headers=bearer(renewed))).status_code == 200
        assert (await client.get("/api/auth/me", headers=bearer(renewed))).json()[
            "must_change_password"
        ] is False
        # Пропуск, выданный с разовым паролем, смена закрыла.
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 403


class TestPassAfterPasswordChange:
    """Пропуск, выданный до смены пароля, не действует (проверка прода 10.10.2026): окно
    сброса обещало «старый перестанет действовать», а пропуск открывал данные до конца
    суток — и снова, едва человек сменит разовый пароль."""

    async def test_own_change_closes_every_older_pass_and_gives_a_new_one(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        """Два сеанса — две вкладки или два устройства; смена в одном закрывает оба
        прежних пропуска, а сменивший работает дальше новым."""
        await make_user("ivan@site.com")
        here = await sign_in("ivan@site.com")
        elsewhere = await sign_in("ivan@site.com")

        changed = await client.post("/api/auth/password", headers=bearer(here), json=RIGHT_OLD)

        assert changed.status_code == 200, changed.text
        for old in (here, elsewhere):
            refused = await client.get("/api/auth/me", headers=bearer(old))
            assert refused.status_code == 403
            assert "Пароль учётки сменили" in refused.json()["detail"]
            assert refused.headers["X-Access-Refusal"] == "signed-out"
        renewed = await client.get("/api/auth/me", headers=bearer(changed.json()["token"]))
        assert renewed.status_code == 200
        assert renewed.json()["email"] == "ivan@site.com"

    async def test_pass_from_before_stamps_lives_its_term(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        """Пропуск, выданный до выкатки отпечатков, — без отпечатка: он доживает свой срок
        (сутки), а не выбрасывает всех из сервиса в день выкатки."""
        user = await make_user("ivan@site.com")
        moment = int(datetime.now(UTC).timestamp())
        legacy = jwt.encode(
            {"sub": str(user.id), "iat": moment, "exp": moment + 3600}, "x" * 64, algorithm="HS256"
        )

        response = await client.get("/api/auth/me", headers=bearer(legacy))

        assert response.status_code == 200, response.text


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
