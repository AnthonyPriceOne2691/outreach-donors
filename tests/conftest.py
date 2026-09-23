"""Оснастка тестов, которым нужна настоящая база.

Ядро тестируется без сети и без сервера — так записано в конституции, и сотня
тестов идёт за две десятых секунды. Репозиторий исключение по существу:
проверять запрос к базе на подделке значит проверять подделку.

База настоящая, но отдельная — `outreach_test`. Каждый тест работает во внешней
транзакции, которая откатывается: тесты не видят следов друг друга и не зависят
от порядка.

Про циклы событий. Схема поднимается один раз собственным `asyncio.run`, а не
асинхронной фикстурой уровня сессии: соединение asyncpg привязано к тому циклу,
в котором создано, а каждому тесту достаётся свой. Движок поэтому тоже
пофункциональный.

Схему поднимает **`alembic upgrade head`, а не `create_all`**. Разница не
стилистическая: `create_all` строит базу по моделям, то есть проверяет модели
сами собой, а в прод едет цепочка миграций — единственный шаг развёртывания,
который до этого не исполнялся ни разу. Расхождение между моделями и цепочкой
при `create_all` невидимо: тесты зелёные, а `upgrade head` на чистой базе даёт
не ту схему. Поэтому здесь ровно та команда, что и в проде, и на пустой схеме.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from backend.api import deps
from backend.api.app import create_app
from backend.config import outreach as outreach_cfg
from backend.features.access.attempts import LoginAttempts
from backend.features.access.repository import AccessRepository
from backend.features.core import models  # noqa: F401  — регистрирует таблицы
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    SenderStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import SenderModel
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_ROOT = Path(__file__).resolve().parent.parent

TEST_DSN = os.getenv(
    "TEST_STORAGE_DSN",
    "postgresql+asyncpg://outreach:outreach@localhost:5442/outreach_test",
)


async def _empty_the_database() -> None:
    """Снести схему целиком, включая `alembic_version`.

    `drop_all` по моделям оставил бы таблицы, которых в моделях уже нет, и
    отметку о последней миграции — то есть база была бы не чистой, а
    «как сложилось». Ровно этот класс прячет миграцию, которая не доезжает
    с нуля, но проходит на дев-базе.
    """
    engine = create_async_engine(TEST_DSN, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


def _migrate_to_head() -> None:
    """Та же команда, что и в проде, тем же способом получения адреса базы."""
    env = {**os.environ, "STORAGE_DSN": TEST_DSN}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,  # разбираем код сами: нужно своё сообщение, а не CalledProcessError
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head не прошёл на чистой базе — это отказ развёртывания, "
            f"а не теста.\n{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Схема создаётся один раз на прогон, в своём цикле событий."""
    asyncio.run(_empty_the_database())
    _migrate_to_head()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DSN)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия во внешней транзакции. Тест может коммитить внутри — откатывается
    внешняя, и база остаётся чистой."""
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with factory() as s:
        yield s
    await transaction.rollback()
    await connection.close()


# --- оснастка веб-слоя ---
#
# Приложение поднимается в памяти, без сети и без сервера: запрос идёт прямо
# в ASGI. Базу оно берёт ту же, что и остальные тесты, — через подмену
# зависимости: иначе маршрут писал бы в настоящую базу разработчика, а тест
# не видел бы записанного.


@pytest.fixture(autouse=True)
def _no_waiting_between_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторы проверяются числом попыток, а не часами.

    Паузы между попытками растут по степени двойки; с настоящими
    паузами набор тестов идёт минуту вместо секунды, и первым, что
    захочется сделать, станет выключить повторы в коде.
    """

    async def instant(_seconds: float) -> None:
        return None

    async def no_waiting(_self: object) -> None:
        return None

    # Гасится имя в модуле повторов, а не `asyncio.sleep` целиком: второе
    # правит сам модуль asyncio и молча ускоряет любой другой сон в проекте.
    # Поймано тестом паузы обхода: он видел ноль вместо пятидесяти
    # миллисекунд и выглядел как дефект ограничителя.
    monkeypatch.setattr("backend.shared.net.retry._sleep", instant)
    # Ограничитель частоты гасится целиком, а не через сон: его окно
    # считается по часам, и «сон без сна» превращает ожидание
    # в холостой цикл на настоящую минуту. Поймано ровно так: набор
    # из двадцати тестов стал идти минуту.
    monkeypatch.setattr("backend.shared.net.retry.RateLimiter.acquire", no_waiting)


@pytest.fixture
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Секрет подписи. Без него приложение не собирается — и это проверяется
    отдельным тестом, а не обходится умолчанием."""
    monkeypatch.setattr("backend.config.access.JWT_SECRET", "x" * 64)


@pytest.fixture
def api_app(session: AsyncSession, jwt_secret: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # Свой счётчик попыток на каждый тест: общий на процесс копил бы
    # неудачи между тестами, и порядок запуска начал бы значить.
    monkeypatch.setattr(deps, "attempts", LoginAttempts(limit=5))

    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: session
    return app


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=api_app), base_url="http://test"
    ) as http_client:
        yield http_client


@pytest.fixture
def make_user(session: AsyncSession) -> Callable[..., Awaitable[UserModel]]:
    """Завести учётку прямо в базе, минуя маршруты: тесту нужен вошедший,
    а не проверка заведения."""

    async def _make(
        email: str,
        *,
        role: UserRole = UserRole.OPERATOR,
        password: str = "пароль-для-теста",
        is_active: bool = True,
        must_change_password: bool = False,
        permissions: dict[str, bool] | None = None,
    ) -> UserModel:
        repository = AccessRepository(session)
        user = await repository.create(email=email, password=password, role=role)
        await session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                is_active=is_active,
                must_change_password=must_change_password,
                permissions=permissions,
            )
        )
        await session.commit()
        await session.refresh(user)
        return user

    return _make


@pytest.fixture
def sign_in(client: AsyncClient) -> Callable[..., Awaitable[str]]:
    """Войти и получить пропуск тем же путём, каким его получает интерфейс."""

    async def _sign_in(email: str, password: str = "пароль-для-теста") -> str:
        response = await client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        token = response.json()["token"]
        assert isinstance(token, str)
        return token

    return _sign_in


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


#: Заполненный юридический блок. Без него отправка отказывает — это
#: проверяется отдельным тестом.
FILLED = {
    "OUTREACH_SENDER_NAME": "Anna Ro",
    "OUTREACH_POSTAL_ADDRESS": "1 Main Street, Dublin",
    "OUTREACH_UNSUBSCRIBE_URL": "https://ours.test/stop",
    # Им подписана метка донора в ссылке отписки: без секрета ссылка
    # не собирается, и юридический блок остаётся незаполненным.
    "OUTREACH_INBOUND_SECRET": "unsubscribe-secret",
}


@pytest.fixture
def filled_legal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заполнить то, без чего письмо отправлять нельзя."""
    monkeypatch.setattr(outreach_cfg, "SENDER_NAME", FILLED["OUTREACH_SENDER_NAME"])
    monkeypatch.setattr(outreach_cfg, "POSTAL_ADDRESS", FILLED["OUTREACH_POSTAL_ADDRESS"])
    monkeypatch.setattr(outreach_cfg, "UNSUBSCRIBE_URL", FILLED["OUTREACH_UNSUBSCRIBE_URL"])
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", FILLED["OUTREACH_INBOUND_SECRET"])


async def make_donor(
    session: AsyncSession,
    host: str,
    *,
    email: str | None = None,
    dr: int = 30,
    review: str | None = "accepted",
) -> DomainModel:
    """Подходящий донор, при желании с адресом.

    По умолчанию — принятый человеком: контакты и письма получают только
    принятых, и «донор, готовый к работе» в тестах значит именно это.
    `review=None` — годный по порогам, но ещё не рассмотренный.
    """
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=dr, review=review))
    if email is not None:
        session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE))
    await session.flush()
    return domain


async def make_sender(session: AsyncSession, email: str, *, cap: int = 20) -> SenderModel:
    """Ящик, которым можно писать сегодня."""
    sender = SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=Stage.DONORS,
        daily_cap=cap,
        status=SenderStatus.FREE,
        enabled=True,
        warmup_started_at=None,
    )
    session.add(sender)
    await session.flush()
    return sender
