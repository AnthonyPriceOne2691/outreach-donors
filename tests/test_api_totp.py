"""Вход с кодом по HTTP: второй шаг, обязательное включение, своё включение и выключение, сброс.

Вход у сервиса один — `POST /api/auth/login`, им ходят и экран, и API. Поэтому главное здесь —
что без второго шага пропуска не получает никто: ни ответ входа, ни вызов второго шага, ни вызов
чужого шага пропуском не служат. Остальное — то, что ломается тихо: неверный код без счёта
попыток и паузы, код восстановления, который пускает дважды, включение, после которого живут
старые пропуска, сброс админом без его пароля.
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.api import deps
from backend.config import access as cfg
from backend.features.access.attempts import LoginAttempts
from backend.features.access import recovery_codes, second_step, totp
from backend.features.access.challenge import STALE
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]

IVAN = "ivan@team.example.test"
ADMIN = "admin@team.example.test"
PASSWORD = "пароль-для-теста"


@pytest.fixture(autouse=True)
def code_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ шифрования есть, обязательных ролей нет — как по умолчанию после выкатки с ключом."""
    monkeypatch.setattr(cfg, "TOTP_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset())
    monkeypatch.setattr(cfg, "LOCKOUT_AFTER_FAILURES", 5)
    monkeypatch.setattr(cfg, "LOCKOUT_FIRST_MINUTES", 1)


def now_code(secret: str) -> str:
    return totp.code_at(secret, time.time())


def next_code(secret: str) -> str:
    """Код следующего шага: тоже в окне допуска, но шаг позже — повтором не считается."""
    return totp.code_at(secret, time.time() + totp.STEP_SECONDS)


def wrong_code(secret: str) -> str:
    """Шесть цифр, которые не подходят ни к одному шагу окна."""
    moment = time.time()
    near = {totp.code_at(secret, moment + s * totp.STEP_SECONDS) for s in range(-2, 3)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in near)


async def with_code(session: AsyncSession, user: UserModel) -> tuple[str, list[str]]:
    """Включить код учётке прямо в базе: секрет и коды восстановления."""
    secret = await second_step.begin(session, user)
    await session.refresh(user)
    codes = await second_step.enable(session, user, now_code(secret), datetime.now(UTC))
    await session.commit()
    return secret, codes


async def password_step(client: AsyncClient, email: str = IVAN) -> Response:
    return await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


async def challenge_of(client: AsyncClient, email: str = IVAN) -> str:
    response = await password_step(client, email)
    assert response.status_code == 403, response.text
    challenge = response.json()["challenge"]
    assert isinstance(challenge, str)
    return challenge


async def _actions(session: AsyncSession) -> list[AuditAction]:
    rows = await session.execute(select(AuditLogModel.action).order_by(AuditLogModel.id))
    return list(rows.scalars().all())


class TestPasswordStep:
    async def test_without_a_code_login_is_as_before(
        self, client: AsyncClient, make_user: MakeUser
    ) -> None:
        await make_user(IVAN)

        response = await password_step(client)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token"]
        assert "step" not in body
        assert body["user"]["totp_enabled"] is False
        assert body["user"]["totp_required"] is False

    async def test_with_a_code_the_pair_gives_a_challenge_not_a_pass(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        await with_code(session, user)

        response = await password_step(client)

        assert response.status_code == 403
        assert response.headers["X-Access-Refusal"] == "second-step"
        body = response.json()
        assert set(body) == {"detail", "step", "challenge", "expires_in_minutes"}
        assert body["step"] == "code"
        assert body["expires_in_minutes"] == 5
        assert "код из приложения" in body["detail"]
        # Пара подошла, а входа ещё нет: ни записи «Вход», ни обнулённого счёта неудач.
        assert AuditAction.LOGIN not in await _actions(session)

    async def test_challenge_is_not_a_pass_anywhere(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN, role=UserRole.ADMIN)
        await with_code(session, user)
        challenge = await challenge_of(client, IVAN)

        for path in ("/api/auth/me", "/api/users", "/api/journal"):
            for headers in (bearer(challenge), {"X-Access-Token": challenge}):
                refused = await client.get(path, headers=headers)
                assert refused.status_code == 403, (path, refused.text)
                assert refused.headers["X-Access-Refusal"] == "signed-out"

    async def test_a_pass_is_not_a_challenge(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        await make_user(IVAN)
        token = (await password_step(client)).json()["token"]

        response = await client.post(
            "/api/auth/login/code", json={"challenge": token, "code": "123456"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == STALE


class TestCodeStep:
    async def test_right_code_gives_the_pass_and_only_then_the_login_record(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        challenge = await challenge_of(client)

        response = await client.post(
            "/api/auth/login/code", json={"challenge": challenge, "code": next_code(secret)}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["email"] == IVAN
        assert body["user"]["totp_enabled"] is True
        me = await client.get("/api/auth/me", headers=bearer(body["token"]))
        assert me.status_code == 200, me.text
        assert (await _actions(session)).count(AuditAction.LOGIN) == 1

    async def test_challenge_works_once(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        secret, codes = await with_code(session, user)
        challenge = await challenge_of(client)
        first = await client.post(
            "/api/auth/login/code", json={"challenge": challenge, "code": next_code(secret)}
        )
        assert first.status_code == 200, first.text

        again = await client.post(
            "/api/auth/login/code", json={"challenge": challenge, "code": codes[0]}
        )

        assert again.status_code == 400
        assert again.json()["detail"] == STALE

    async def test_code_challenge_does_not_start_enrollment(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        await with_code(session, user)
        challenge = await challenge_of(client)

        response = await client.post("/api/auth/login/enroll", json={"challenge": challenge})

        assert response.status_code == 400
        assert response.json()["detail"] == STALE

    async def test_wrong_code_is_counted_like_a_wrong_password(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Счётчик в памяти (в тестах — пять в минуту): шестая попытка до сверки не доходит."""
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        challenge = await challenge_of(client)
        for _ in range(5):
            await client.post(
                "/api/auth/login/code", json={"challenge": challenge, "code": wrong_code(secret)}
            )

        stopped = await client.post(
            "/api/auth/login/code", json={"challenge": challenge, "code": next_code(secret)}
        )

        assert stopped.status_code == 429
        assert "Слишком много попыток входа" in stopped.json()["detail"]

    async def test_wrong_code_is_a_failed_login_and_pauses_the_account(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Пауза учётки в базе — та же, что после неверных паролей; счётчик в памяти здесь шире,
        чтобы до неё дойти."""
        monkeypatch.setattr(deps, "attempts", LoginAttempts(limit=100))
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        challenge = await challenge_of(client)

        refusals = [
            await client.post(
                "/api/auth/login/code", json={"challenge": challenge, "code": wrong_code(secret)}
            )
            for _ in range(5)
        ]

        assert [r.status_code for r in refusals] == [400, 400, 400, 400, 400]
        assert "Код не подошёл" in refusals[0].json()["detail"]
        failed = (
            await session.scalars(
                select(AuditLogModel.details).where(
                    AuditLogModel.action == AuditAction.LOGIN_FAILED
                )
            )
        ).all()
        assert [d["reason"] for d in failed if d] == ["код не подошёл"] * 5
        assert AuditAction.LOGIN_PAUSED in await _actions(session)
        # Пауза держит и верный код: во время неё код не сверяется.
        paused = await client.post(
            "/api/auth/login/code", json={"challenge": challenge, "code": next_code(secret)}
        )
        assert paused.status_code == 429
        assert "попробуйте через 1 мин" in paused.json()["detail"]

    async def test_the_same_code_twice_is_refused(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        code = next_code(secret)
        signed = await client.post(
            "/api/auth/login/code", json={"challenge": await challenge_of(client), "code": code}
        )
        assert signed.status_code == 200, signed.text

        again = await client.post(
            "/api/auth/login/code", json={"challenge": await challenge_of(client), "code": code}
        )

        assert again.status_code == 400
        assert "уже использован" in again.json()["detail"]

    async def test_recovery_code_lets_in_once(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        _, codes = await with_code(session, user)

        first = await client.post(
            "/api/auth/login/code",
            json={"challenge": await challenge_of(client), "code": codes[3].upper()},
        )
        again = await client.post(
            "/api/auth/login/code",
            json={"challenge": await challenge_of(client), "code": codes[3]},
        )

        assert first.status_code == 200, first.text
        assert again.status_code == 400
        used = (
            await session.scalars(
                select(AuditLogModel.details).where(
                    AuditLogModel.action == AuditAction.RECOVERY_CODE_USED
                )
            )
        ).all()
        assert [d["осталось кодов"] for d in used if d] == [recovery_codes.COUNT - 1]


class TestRequired:
    async def test_role_that_requires_a_code_must_turn_it_on(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset({"admin"}))
        await make_user(ADMIN, role=UserRole.ADMIN)

        step = await password_step(client, ADMIN)
        assert step.status_code == 403
        assert step.json()["step"] == "enroll"
        challenge = step.json()["challenge"]

        setup = await client.post("/api/auth/login/enroll", json={"challenge": challenge})
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]
        assert setup.json()["uri"].startswith("otpauth://totp/")

        wrong = await client.post(
            "/api/auth/login/enroll/confirm",
            json={"challenge": challenge, "code": wrong_code(secret)},
        )
        assert wrong.status_code == 400

        done = await client.post(
            "/api/auth/login/enroll/confirm",
            json={"challenge": challenge, "code": now_code(secret)},
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert len(body["recovery_codes"]) == recovery_codes.COUNT
        assert body["user"]["totp_enabled"] is True
        assert body["user"]["totp_required"] is True
        me = await client.get("/api/auth/me", headers=bearer(body["token"]))
        assert me.status_code == 200, me.text
        assert AuditAction.TOTP_ENABLED in await _actions(session)
        # Включение тратит вызов: второй раз им код не включить и не войти.
        again = await client.post(
            "/api/auth/login/enroll/confirm",
            json={"challenge": challenge, "code": next_code(secret)},
        )
        assert again.status_code == 400

    async def test_required_code_cannot_be_turned_off(
        self,
        client: AsyncClient,
        session: AsyncSession,
        make_user: MakeUser,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await make_user(ADMIN, role=UserRole.ADMIN)
        secret, _ = await with_code(session, user)
        token = (
            await client.post(
                "/api/auth/login/code",
                json={"challenge": await challenge_of(client, ADMIN), "code": next_code(secret)},
            )
        ).json()["token"]
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset({"admin"}))

        refused = await client.post(
            "/api/auth/totp/disable",
            json={"password": PASSWORD, "code": now_code(secret)},
            headers=bearer(token),
        )

        assert refused.status_code == 409
        assert "обязателен" in refused.json()["detail"]
        await session.refresh(user)
        assert second_step.enabled(user)


class TestOwnCode:
    async def test_turning_on_closes_old_passes_and_gives_a_new_one(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        await make_user(IVAN)
        old = (await password_step(client)).json()["token"]
        other = (await password_step(client)).json()["token"]

        refused = await client.post(
            "/api/auth/totp/setup", json={"password": "не тот"}, headers=bearer(old)
        )
        assert refused.status_code == 403
        assert refused.headers["X-Access-Refusal"] == "confirm-password"

        setup = await client.post(
            "/api/auth/totp/setup", json={"password": PASSWORD}, headers=bearer(old)
        )
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]
        done = await client.post(
            "/api/auth/totp/confirm", json={"code": now_code(secret)}, headers=bearer(old)
        )

        assert done.status_code == 200, done.text
        assert len(done.json()["recovery_codes"]) == recovery_codes.COUNT
        for stale in (old, other):
            gone = await client.get("/api/auth/me", headers=bearer(stale))
            assert gone.status_code == 403
            assert "вход с кодом включили" in gone.json()["detail"]
        fresh = await client.get("/api/auth/me", headers=bearer(done.json()["token"]))
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["totp_enabled"] is True

    async def test_turning_off_takes_password_and_code(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        token = (
            await client.post(
                "/api/auth/login/code",
                json={"challenge": await challenge_of(client), "code": next_code(secret)},
            )
        ).json()["token"]

        wrong = await client.post(
            "/api/auth/totp/disable",
            json={"password": PASSWORD, "code": wrong_code(secret)},
            headers=bearer(token),
        )
        assert wrong.status_code == 400
        off = await client.post(
            "/api/auth/totp/disable",
            json={"password": PASSWORD, "code": totp.code_at(secret, time.time() - 30)},
            headers=bearer(token),
        )

        # Код прошлого шага — уже принятого шага раньше: повтор, а не выключение.
        assert off.status_code == 400
        codes = await recovery_codes.issue(session, user.id)
        await session.commit()
        off = await client.post(
            "/api/auth/totp/disable",
            json={"password": PASSWORD, "code": codes[0]},
            headers=bearer(token),
        )
        assert off.status_code == 200, off.text
        assert off.json()["totp_enabled"] is False
        assert AuditAction.TOTP_DISABLED in await _actions(session)
        assert (await password_step(client)).status_code == 200


class TestAdminReset:
    async def test_reset_takes_the_admins_password_and_closes_the_passes(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        admin = await make_user(ADMIN, role=UserRole.ADMIN)
        user = await make_user(IVAN)
        secret, _ = await with_code(session, user)
        ivan = (
            await client.post(
                "/api/auth/login/code",
                json={"challenge": await challenge_of(client), "code": next_code(secret)},
            )
        ).json()["token"]
        boss = (await password_step(client, ADMIN)).json()["token"]
        path = f"/api/users/{user.id}/totp/reset"

        bare = await client.post(path, headers=bearer(boss))
        wrong = await client.post(path, json={"current_password": "мимо"}, headers=bearer(boss))
        assert [bare.status_code, wrong.status_code] == [403, 403]
        await session.refresh(user)
        assert second_step.enabled(user)

        done = await client.post(path, json={"current_password": PASSWORD}, headers=bearer(boss))

        assert done.status_code == 200, done.text
        assert done.json()["totp_enabled"] is False
        assert (await client.get("/api/auth/me", headers=bearer(ivan))).status_code == 403
        assert (await password_step(client)).status_code == 200
        reset = (
            await session.scalars(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.TOTP_RESET)
            )
        ).one()
        assert (reset.user_id, reset.target) == (admin.id, f"user:{user.id}")
        listed = await client.get("/api/users", headers=bearer(boss))
        assert {card["email"]: card["totp_enabled"] for card in listed.json()} == {
            ADMIN: False,
            IVAN: False,
        }

    async def test_own_code_is_not_reset_and_nothing_to_reset_is_said(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser
    ) -> None:
        admin = await make_user(ADMIN, role=UserRole.ADMIN)
        user = await make_user(IVAN)
        boss = (await password_step(client, ADMIN)).json()["token"]

        own = await client.post(
            f"/api/users/{admin.id}/totp/reset",
            json={"current_password": PASSWORD},
            headers=bearer(boss),
        )
        nothing = await client.post(
            f"/api/users/{user.id}/totp/reset",
            json={"current_password": PASSWORD},
            headers=bearer(boss),
        )

        assert own.status_code == 409
        assert "выключают паролем и кодом" in own.json()["detail"]
        assert nothing.status_code == 409
        assert "сбрасывать нечего" in nothing.json()["detail"]


class TestNoKey:
    async def test_without_a_key_turning_on_is_refused_in_words(
        self, client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await make_user(IVAN)
        token = (await password_step(client)).json()["token"]
        monkeypatch.setattr(cfg, "TOTP_KEY", "")

        refused = await client.post(
            "/api/auth/totp/setup", json={"password": PASSWORD}, headers=bearer(token)
        )

        assert refused.status_code == 409
        assert "AUTH_TOTP_KEY" in refused.json()["detail"]
