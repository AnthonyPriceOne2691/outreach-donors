"""Пауза входа после неудач подряд и срок разового пароля (аудит 20.11.2026).

Две дыры одного рода — ключ от учётки, который живёт дольше, чем должен. Подбор, растянутый
на сутки, проходил под счётчиком в памяти (десять попыток в минуту — четырнадцать тысяч в
сутки), а выкатка счётчик обнуляла. Разовый пароль из переписки пускал, пока его не сменят, —
то есть, если человек так и не вошёл, всегда. Время подаётся снаружи (`login(now=…)`): тест,
который ждёт паузу, не запускают.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.access.administration import (
    change_own_password,
    create_user,
    reset_password,
)
from backend.features.access.lockout import (
    AccountPausedError,
    pause_after,
    refuse_while_paused,
)
from backend.features.access.login import EXPIRED, LoginFailedError, OneTimeExpiredError, login
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up, load_migration

MakeUser = Callable[..., Awaitable[UserModel]]

#: Минута первой неудачи. Некруглая — не дата-обязательство.
NOW = datetime(2026, 11, 20, 10, 17, 43, tzinfo=UTC)
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)

#: Учётки тестов — адреса из зоны для примеров.
IVAN = "ivan@team.example.test"
ADMIN = "admin@team.example.test"
PASSWORD = "пароль-для-теста"

REVISION = "c0cf586c2995_account_pause_and_one_time_expiry.py"


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch, jwt_secret: None) -> None:
    """Правила — умолчаниями, а не тем, что лежит в `.env` разработчика."""
    monkeypatch.setattr("backend.config.access.LOCKOUT_AFTER_FAILURES", 5)
    monkeypatch.setattr("backend.config.access.LOCKOUT_FIRST_MINUTES", 1)
    monkeypatch.setattr("backend.config.access.LOCKOUT_MAX_MINUTES", 30)
    monkeypatch.setattr("backend.config.access.ONE_TIME_TTL_HOURS", 72)


class TestPauseRule:
    @pytest.mark.parametrize(
        ("failures", "minutes"),
        [(4, None), (5, 1), (6, 2), (7, 4), (8, 8), (9, 16), (10, 30), (11, 30), (100_000, 30)],
    )
    def test_pause_starts_at_the_fifth_and_doubles_up_to_half_an_hour(
        self, failures: int, minutes: int | None
    ) -> None:
        pause = pause_after(failures)

        assert (pause / MINUTE if pause is not None else None) == minutes

    def test_refusal_names_whole_minutes_and_the_seconds_to_wait(self) -> None:
        """Минуты — вверх до целой: «через 0 мин» человеку не скажешь. Секунды — заголовку
        `Retry-After`, как у счётчика в памяти."""
        with pytest.raises(AccountPausedError) as paused:
            refuse_while_paused(NOW + MINUTE + SECOND, NOW)

        assert str(paused.value) == "Слишком много неудачных попыток — попробуйте через 2 мин"
        assert paused.value.retry_after_seconds == 61

    @pytest.mark.parametrize(
        "until", [None, NOW, NOW - SECOND], ids=["не было", "кончилась", "давно"]
    )
    def test_no_pause_or_an_ended_one_lets_through(self, until: datetime | None) -> None:
        refuse_while_paused(until, NOW)


async def _fail(session: AsyncSession, email: str, times: int, *, at: datetime = NOW) -> None:
    for _ in range(times):
        with pytest.raises(LoginFailedError):
            await login(session, email, "мимо", now=at)


async def _journal(session: AsyncSession, action: AuditAction) -> list[AuditLogModel]:
    rows = await session.execute(
        select(AuditLogModel).where(AuditLogModel.action == action).order_by(AuditLogModel.id)
    )
    return list(rows.scalars().all())


async def _fresh(session: AsyncSession, user_id: int) -> UserModel:
    """Учётка, как она лежит в базе, — не копия, которую держит сессия."""
    rows = await session.execute(
        select(UserModel).where(UserModel.id == user_id).execution_options(populate_existing=True)
    )
    return rows.scalar_one()


class TestPauseOnLogin:
    async def test_fourth_failure_does_not_pause_the_fifth_does(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)

        await _fail(session, IVAN, 4)
        assert (await _fresh(session, user.id)).locked_until is None

        await _fail(session, IVAN, 1)
        paused = await _fresh(session, user.id)
        assert paused.failed_logins == 5
        assert paused.locked_until == NOW + MINUTE

    async def test_right_password_during_the_pause_is_refused_and_not_checked(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Верный пароль во время паузы — тоже отказ: иначе подбирающий узнал бы его по ответу
        «вошёл». И отказ не считается неудачей и не пишется в журнал — только начало паузы."""
        user = await make_user(IVAN)
        await _fail(session, IVAN, 5)
        written = len(await _journal(session, AuditAction.LOGIN_FAILED))

        with pytest.raises(AccountPausedError) as paused:
            await login(session, IVAN, PASSWORD, now=NOW + 30 * SECOND)

        assert str(paused.value) == "Слишком много неудачных попыток — попробуйте через 1 мин"
        assert paused.value.retry_after_seconds == 30
        assert (await _fresh(session, user.id)).failed_logins == 5
        assert len(await _journal(session, AuditAction.LOGIN_FAILED)) == written

    async def test_after_the_pause_the_right_password_works_and_clears_the_count(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        await _fail(session, IVAN, 5)

        opened = await login(session, IVAN, PASSWORD, now=NOW + MINUTE)

        assert opened.user_id == user.id
        cleared = await _fresh(session, user.id)
        assert (cleared.failed_logins, cleared.locked_until) == (0, None)

    async def test_each_further_failure_doubles_the_pause(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Каждая неудача после паузы — пауза вдвое: подбор сходит до пары попыток в час."""
        user = await make_user(IVAN)
        await _fail(session, IVAN, 5)
        moment, pauses = NOW, []
        for _ in range(6):
            moment = (await _fresh(session, user.id)).locked_until or moment
            await _fail(session, IVAN, 1, at=moment)
            pauses.append(((await _fresh(session, user.id)).locked_until or moment) - moment)

        assert [pause / MINUTE for pause in pauses] == [2, 4, 8, 16, 30, 30]

    async def test_pause_start_is_in_the_journal_in_words(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)

        await _fail(session, IVAN, 5)

        [record] = await _journal(session, AuditAction.LOGIN_PAUSED)
        assert record.user_id == user.id
        assert record.target == f"user:{user.id}"
        assert record.details == {"email": IVAN, "неудач подряд": 5, "пауза": "1 мин"}

    async def test_pause_is_kept_by_the_database_not_by_the_process(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Счёт и пауза — в базе: новый процесс после выкатки видит ту же паузу. Здесь — та же
        база после коммита и без единой учётки в памяти сессии."""
        await make_user(IVAN)
        await _fail(session, IVAN, 5)
        await session.commit()
        session.expunge_all()

        with pytest.raises(AccountPausedError):
            await login(session, IVAN, PASSWORD, now=NOW + 10 * SECOND)

    async def test_unknown_email_creates_no_rows_and_gets_no_pause(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Учётки нет — нечему вставать на паузу, и строка на каждую набранную почту не
        заводится. Отказ — прежний, «неверная почта или пароль»."""
        await make_user(IVAN)
        before = await session.scalar(select(func.count()).select_from(UserModel))

        await _fail(session, "никого@team.example.test", 12)

        assert await session.scalar(select(func.count()).select_from(UserModel)) == before
        assert await _journal(session, AuditAction.LOGIN_PAUSED) == []

    async def test_admin_reset_lifts_the_pause(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Человек на паузе просит админа — и с новым разовым входит сразу: неудачи подбирали
        прежний пароль. Время — от часов машины: срок нового разового идёт с них же."""
        admin = await make_user(ADMIN, role=UserRole.ADMIN)
        user = await make_user(IVAN)
        start = datetime.now(UTC)
        await _fail(session, IVAN, 5, at=start)

        _, issued = await reset_password(session, user_id=user.id, author=actor_of(admin))

        assert (await login(session, IVAN, issued, now=start + SECOND)).user_id == user.id

    async def test_own_password_change_clears_the_count(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user(IVAN)
        await _fail(session, IVAN, 4)

        await change_own_password(
            session, user_id=user.id, current=PASSWORD, new="три слова подряд длиннее"
        )

        assert (await _fresh(session, user.id)).failed_logins == 0


class TestOneTimePasswordTerm:
    async def _issued(self, session: AsyncSession, make_user: MakeUser) -> tuple[UserModel, str]:
        admin = await make_user(ADMIN, role=UserRole.ADMIN)
        return await create_user(
            session, email=IVAN, role=UserRole.OPERATOR, author=actor_of(admin)
        )

    async def test_one_time_password_works_within_its_term(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user, issued = await self._issued(session, make_user)

        opened = await login(session, IVAN, issued, now=user.password_set_at + 71 * HOUR)

        assert opened.must_change_password

    async def test_expired_one_time_password_is_refused_in_words(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user, issued = await self._issued(session, make_user)

        with pytest.raises(OneTimeExpiredError) as expired:
            await login(session, IVAN, issued, now=user.password_set_at + 72 * HOUR + SECOND)

        assert str(expired.value) == EXPIRED
        [record] = [
            entry
            for entry in await _journal(session, AuditAction.LOGIN_FAILED)
            if entry.user_id == user.id
        ]
        assert record.details == {"email": IVAN, "reason": "разовый пароль истёк"}
        # Пара подошла — это не неудача подбора: счёт не растёт.
        assert (await _fresh(session, user.id)).failed_logins == 0

    async def test_wrong_password_on_an_expired_account_says_nothing_new(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        """Об истёкшем разовом узнаёт только тот, кто его знает: без пароля — прежний отказ."""
        user, _ = await self._issued(session, make_user)

        with pytest.raises(LoginFailedError) as refused:
            await login(session, IVAN, "мимо", now=user.password_set_at + 100 * HOUR)

        assert type(refused.value) is LoginFailedError
        assert str(refused.value) == "Неверная почта или пароль"

    async def test_own_password_has_no_term(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user, issued = await self._issued(session, make_user)
        await change_own_password(
            session, user_id=user.id, current=issued, new="три слова подряд длиннее"
        )

        opened = await login(
            session, IVAN, "три слова подряд длиннее", now=user.password_set_at + 1000 * HOUR
        )

        assert not opened.must_change_password

    async def test_reset_gives_a_fresh_term(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user, issued = await self._issued(session, make_user)
        await session.execute(
            text("UPDATE users SET password_set_at = now() - interval '100 hours' WHERE id = :id"),
            {"id": user.id},
        )
        await session.refresh(user)
        with pytest.raises(OneTimeExpiredError):
            await login(session, IVAN, issued)

        admin = await AccessRepository(session).by_email(ADMIN)
        assert admin is not None
        _, again = await reset_password(session, user_id=user.id, author=actor_of(admin))

        assert (await login(session, IVAN, again)).must_change_password


def _revision(connection: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(load_migration(REVISION), step)()


class TestMigration:
    async def test_columns_go_away_and_come_back(self, session: AsyncSession) -> None:
        connection = await session.connection()
        columns = {"password_set_at", "failed_logins", "locked_until"}

        down, up = await connection.run_sync(
            lambda sync: columns_down_and_up(sync, REVISION, "users", columns)
        )

        assert (down, up) == (set(), columns)

    async def test_accounts_made_before_get_their_term_from_the_deploy(
        self, session: AsyncSession
    ) -> None:
        """Учётка с разовым паролем, заведённая до ревизии, не заперта выкаткой: срок ей идёт
        с минуты миграции, счёт неудач — ноль, паузы нет."""
        connection = await session.connection()
        await connection.run_sync(_revision, "downgrade")
        await session.execute(
            text(
                "INSERT INTO users (email, password_hash, role, is_active, must_change_password) "
                "VALUES ('old@team.example.test', 'x', 'operator', true, true)"
            )
        )

        await connection.run_sync(_revision, "upgrade")

        row = (
            await session.execute(
                text(
                    "SELECT password_set_at = now() AS from_deploy, failed_logins, locked_until "
                    "FROM users WHERE email = 'old@team.example.test'"
                )
            )
        ).one()
        assert tuple(row) == (True, 0, None)
