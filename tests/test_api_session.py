"""Сеанс на сервере: выход, простой и пропуск без номера (аудит 20.11.2026, №6 и №7).

Пропуск жил свои сутки целиком: «Выйти» стирал его только в браузере, а вкладка, забытая
открытой, работала до утра. Теперь выход закрывает пропуск на сервере, а час без действий
человека — сеанс. Хранилище сеансов — подделка в памяти (`tests/conftest.SessionStore`), часы
сеансов стоят, пока тест их не сдвинет; суточный срок пропуска проверяет библиотека по
настоящим часам, и сдвиг на часы его не задевает.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from backend.features.access import sessions
from backend.features.access.tokens import (
    REFUSED,
    STAMP_CLAIM,
    TOKEN_HEADER,
    TOKEN_ID_CLAIM,
    password_stamp,
)
from backend.features.core.models.access import UserModel
from httpx import AsyncClient, Response
from tests.conftest import SessionStore, bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Учётка тестов: адрес из зоны для примеров, а не настоящий домен.
OPERATOR = "operator@team.example.test"
#: Запрос, который экран сделал сам: человек за ним последнюю минуту ничего не делал.
BACKGROUND = {sessions.BACKGROUND_HEADER: "1"}

#: Настоящий клиент Redis сеансов: щит `tests/conftest.py` подменяет его на время каждого
#: теста, а модуль тестов собирается раньше.
REAL_CONNECTION = sessions.connection


class Clock:
    """Часы сеансов: стоят, пока тест их не сдвинет."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def move(self, *, minutes: float = 0, seconds: float = 0) -> None:
        self.now += minutes * 60 + seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    ticking = Clock()
    monkeypatch.setattr(sessions, "clock", ticking)
    return ticking


@pytest.fixture
async def token(clock: Clock, make_user: MakeUser, sign_in: SignIn) -> str:
    """Пропуск, выданный только что, — по часам сеансов тоже: часы встают на время выдачи.
    Без этого вход под нагрузкой (bcrypt, общий прогон) шёл секунды, выдача уезжала от часов
    теста, и «минута после входа» оказывалась меньше минуты."""
    await make_user(OPERATOR)
    issued = await sign_in(OPERATOR)
    clock.now = float(jwt.decode(issued, options={"verify_signature": False})["iat"])
    return issued


def _key(pattern: str, token: str) -> str:
    claims = jwt.decode(token, options={"verify_signature": False})
    return pattern.format(token_id=claims[TOKEN_ID_CLAIM])


def _lives_as_long_as(token: str, ttl: int, clock: Clock) -> bool:
    """Срок ключа — до конца срока пропуска по часам сеансов (с точностью до секунды)."""
    expires = jwt.decode(token, options={"verify_signature": False})["exp"]
    return abs(ttl - (expires - clock.now)) <= 1


async def _me(client: AsyncClient, token: str, *, background: bool = False) -> Response:
    headers = {**bearer(token), **(BACKGROUND if background else {})}
    return await client.get("/api/auth/me", headers=headers)


def _signed_out(response: Response, said: str) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["X-Access-Refusal"] == "signed-out"
    assert response.json()["detail"] == said


class TestLogout:
    async def test_logout_closes_this_pass_until_it_expires(
        self, client: AsyncClient, token: str, clock: Clock, session_store: SessionStore
    ) -> None:
        """Копия пропуска — из журнала, со снимка экрана — после выхода больше не пускает.
        Ключ живёт ровно до конца срока пропуска: дальше пропуск закрывает сам срок."""
        out = await client.post("/api/auth/logout", headers=bearer(token))

        assert out.status_code == 204, out.text
        _signed_out(await _me(client, token), "Вы вышли — войдите снова")
        closed = _key(sessions.CLOSED_KEY, token)
        assert session_store.values[closed] == b"logout"
        assert _lives_as_long_as(token, session_store.ttl[closed], clock)

    async def test_logout_leaves_the_other_sessions_alone(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Выход закрывает пропуск этого устройства, а не всю учётку: второй сеанс работает."""
        await make_user(OPERATOR)
        here = await sign_in(OPERATOR)
        elsewhere = await sign_in(OPERATOR)

        await client.post("/api/auth/logout", headers=bearer(here))

        assert (await _me(client, elsewhere)).status_code == 200

    async def test_a_one_time_password_does_not_keep_anyone_in(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """С несменённым разовым паролем открыты только «кто я» и смена пароля — и выход."""
        await make_user(OPERATOR, must_change_password=True)
        fresh = await sign_in(OPERATOR)

        out = await client.post("/api/auth/logout", headers=bearer(fresh))

        assert out.status_code == 204, out.text
        _signed_out(await _me(client, fresh), "Вы вышли — войдите снова")

    async def test_without_redis_the_logout_says_it_was_not_recorded(
        self,
        client: AsyncClient,
        token: str,
        session_store: SessionStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Redis молчит — выход не записан, и ответ говорит это словами, а журнал — строкой
        ошибки: пропуск закроется только сроком. Экран стирает пропуск и тогда."""
        session_store.down = True

        with caplog.at_level(logging.ERROR, logger=sessions.__name__):
            out = await client.post("/api/auth/logout", headers=bearer(token))

        assert out.status_code == 503, out.text
        assert out.json()["detail"] == sessions.NOT_CLOSED
        assert "Redis не ответил" in caplog.text
        session_store.down = False
        assert (await _me(client, token)).status_code == 200


class TestIdle:
    async def test_an_hour_without_actions_ends_the_session(
        self, client: AsyncClient, token: str, clock: Clock, session_store: SessionStore
    ) -> None:
        """Вкладка, забытая открытой: час без действий — и пропуск больше не пускает, хотя
        суточный срок не вышел. Закрыт он тем же ключом, что выход, — с причиной «простой»."""
        clock.move(minutes=61)

        _signed_out(await _me(client, token), sessions.SAID[sessions.IDLE])
        assert session_store.values[_key(sessions.CLOSED_KEY, token)] == b"idle"
        clock.move(seconds=1)
        _signed_out(await _me(client, token), sessions.SAID[sessions.IDLE])

    async def test_actions_keep_the_session_going(
        self, client: AsyncClient, token: str, clock: Clock
    ) -> None:
        """Отсчёт — от последнего действия, а не от входа: три шага по 50 минут проходят, и
        только час тишины после последнего закрывает сеанс."""
        for _ in range(3):
            clock.move(minutes=50)
            assert (await _me(client, token)).status_code == 200

        clock.move(minutes=61)
        _signed_out(await _me(client, token), sessions.SAID[sessions.IDLE])

    async def test_what_the_screen_asks_by_itself_does_not_keep_the_session(
        self, client: AsyncClient, token: str, clock: Clock
    ) -> None:
        """Числа у меню экран спрашивает раз в минуту сам: такие запросы проверяются, но
        простой не прерывают — иначе открытая вкладка не простаивала бы никогда."""
        clock.move(minutes=50)
        assert (await _me(client, token, background=True)).status_code == 200

        clock.move(minutes=11)
        _signed_out(await _me(client, token, background=True), sessions.SAID[sessions.IDLE])

    async def test_the_mark_is_written_at_most_once_a_minute(
        self, client: AsyncClient, token: str, clock: Clock, session_store: SessionStore
    ) -> None:
        """Метка действия — не на каждый запрос: раз в минуту на пропуск, до конца его срока."""
        clock.move(seconds=61)
        await _me(client, token)
        for step in (10, 30, 18):
            clock.move(seconds=step)
            await _me(client, token)
        assert session_store.writes == 1

        clock.move(seconds=3)
        await _me(client, token)

        assert session_store.writes == 2
        assert _lives_as_long_as(token, session_store.ttl[_key(sessions.SEEN_KEY, token)], clock)

    async def test_zero_minutes_leaves_only_the_daily_limit(
        self,
        client: AsyncClient,
        token: str,
        clock: Clock,
        session_store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`ACCESS_IDLE_MINUTES=0` — простой не считается и метки не пишутся: рычаг на случай,
        если счёт простоя мешает работать. Выход при этом работает."""
        monkeypatch.setattr("backend.config.access.IDLE_MINUTES", 0)

        clock.move(minutes=300)

        assert (await _me(client, token)).status_code == 200
        assert session_store.writes == 0


class TestPassWithoutNumber:
    async def test_a_pass_issued_before_the_numbers_lives_out_its_day(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        clock: Clock,
        session_store: SessionStore,
    ) -> None:
        """Пропуск, выданный до выкатки 20.11.2026, номера не несёт: простой у него не
        считается, выход на сервере не записать — он доживает свои сутки, как раньше, а не
        обрывается выкаткой. Хранилище сеансов он не трогает вовсе."""
        user = await make_user(OPERATOR)
        moment = int(datetime.now(UTC).timestamp())
        legacy = jwt.encode(
            {
                "sub": str(user.id),
                "iat": moment,
                "exp": moment + 24 * 3600,
                STAMP_CLAIM: password_stamp(user.password_hash),
            },
            "x" * 64,
            algorithm="HS256",
        )

        clock.move(minutes=300)
        assert (await _me(client, legacy)).status_code == 200
        assert (await client.post("/api/auth/logout", headers=bearer(legacy))).status_code == 204

        assert (await _me(client, legacy)).status_code == 200
        assert session_store.values == {}


class TestSilentRedis:
    async def test_without_redis_the_pass_works_and_the_journal_says_it_once(
        self,
        client: AsyncClient,
        token: str,
        session_store: SessionStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Выход и простой — второй замок поверх суточного срока: молчание Redis не запирает
        всех, а строка ошибки — одна на минуту, а не на каждый запрос."""
        session_store.down = True

        with caplog.at_level(logging.ERROR, logger=sessions.__name__):
            codes = [(await _me(client, token)).status_code for _ in range(3)]

        assert codes == [200] * 3
        complaints = [r for r in caplog.records if "Redis не ответил" in r.getMessage()]
        assert len(complaints) == 1
        assert complaints[0].levelno == logging.ERROR

    def test_the_session_client_waits_a_second_and_is_one_per_process(self) -> None:
        """Redis сеансов ждёт каждый запрос с пропуском: предел — секунда, а не пять, как у
        очереди, и клиент один на процесс — без нового соединения на запрос."""
        REAL_CONNECTION.cache_clear()
        try:
            client = REAL_CONNECTION()
            kwargs = client.connection_pool.connection_kwargs

            assert kwargs["socket_timeout"] == sessions.REDIS_TIMEOUT_SEC == 1.0
            assert kwargs["socket_connect_timeout"] == sessions.REDIS_TIMEOUT_SEC
            assert REAL_CONNECTION() is client
        finally:
            REAL_CONNECTION.cache_clear()


class TestRefusalWords:
    @pytest.mark.parametrize(
        "given",
        ["abc", "a.b.c", jwt.encode({"sub": "1", "exp": 9999999999}, "y" * 64)],
        ids=["не пропуск", "три куска без смысла", "чужая подпись"],
    )
    async def test_a_broken_pass_is_refused_in_russian(
        self, client: AsyncClient, given: str
    ) -> None:
        """Проверка прода 20.11.2026: экран показал «Пропуск не принят: Not enough segments» —
        текст библиотеки по-английски. Теперь — одна фраза словами, без латиницы."""
        response = await client.get("/api/journal", headers={TOKEN_HEADER: given})

        _signed_out(response, REFUSED)
        assert REFUSED == "Пропуск не принят — войдите заново"
        assert re.search(r"[A-Za-z]", response.json()["detail"]) is None

    def test_the_screen_marks_its_own_requests_with_the_same_header(self) -> None:
        """Имя заголовка фонового запроса — одно у сервера и экрана: разъедутся — открытая
        вкладка снова не будет простаивать никогда."""
        client_ts = (
            Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "client.ts"
        )
        found = re.search(
            r"export const BACKGROUND_HEADER = '([^']+)';", client_ts.read_text(encoding="utf-8")
        )

        assert found is not None, "в frontend/src/api/client.ts нет BACKGROUND_HEADER"
        assert found.group(1) == sessions.BACKGROUND_HEADER
