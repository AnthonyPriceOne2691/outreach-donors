"""Очередь, которая уходит сама (`letters/autosend.py`): замечание тест-группы 29.10.2026.

Проверяется то, на чём режим держится: проход — та же пачка, по письму с ящика и с паузой между
его первыми письмами; режим кончается сам, когда очередь пуста; отказ платформы любому письму и
неизвестный исход останавливают проход, а не режим; запуск у очереди один; письмо в журнале — с
тем, кто включил режим; итог пачки при включённом режиме не просит нажать её снова; выключенный
рубильник проходы не пускает.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import AuditAction, MessageStatus, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel, QueueAutosendModel
from backend.features.letters import autosend, autosend_alarm, autosend_window, batch, forecast
from backend.features.letters.transport import NullTransport
from backend.workers import send_jobs
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import make_sender
from tests.migration_helpers import load_migration
from tests.test_letters_send_queue import RefusingAll, Silent, _Closable, _queue

MakeUser = Callable[..., Awaitable[UserModel]]


@pytest.fixture(autouse=True)
def _always_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно получателя у прохода — круглые сутки всю неделю: проверки здесь — не об окне (о нём —
    `TestTheWindow`), и время прогона тестов не должно их решать."""
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(7)))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time.min)
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time.max)


def _now() -> datetime:
    return datetime.now(UTC)


async def _statuses(session: AsyncSession) -> list[MessageStatus]:
    rows = await session.scalars(select(MessageModel.status).order_by(MessageModel.id))
    return list(rows.all())


async def _started(session: AsyncSession, by: int | None = None) -> QueueAutosendModel:
    mode, fresh = await autosend.start(session, Stage.DONORS, "links", by=by, now=_now())
    assert fresh
    await session.commit()
    return mode


class TestThePass:
    async def test_one_letter_per_box_and_then_the_box_rests(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Два ящика, пять писем: проход отдаёт по письму с ящика, следующий сразу — ни одного,
        ящики на паузе; после паузы — снова по письму с ящика."""
        await _queue(session, 5)
        await make_sender(session, "outreach2@mail2.example.test")
        await session.commit()
        mode = await _started(session)

        first = (await autosend.run(session, NullTransport(), mode, now=_now())).state
        again = (await autosend.run(session, NullTransport(), mode, now=_now())).state
        later = (
            await autosend.run(session, NullTransport(), mode, now=_now() + autosend.gap() * 1.6)
        ).state

        assert first.startswith("ушло 2. ящики на паузе между письмами — ближайший освободится в")
        # Свободного ящика нет — очередь не перебирается, сказано, когда освободится первый.
        assert again.startswith("ящики на паузе между письмами — ближайший освободится в")
        assert later.startswith("ушло 2")
        assert (await _statuses(session)).count(MessageStatus.QUEUED) == 1
        assert mode.stopped_at is None
        assert mode.state == later

    async def test_the_empty_queue_finishes_the_mode(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Режим держит только запущенную очередь: кончилась — запуск остановлен проходом, и
        письма следующей сборки ждут нового нажатия."""
        await _queue(session, 1)
        mode = await _started(session)

        sent = (await autosend.run(session, NullTransport(), mode, now=_now())).state
        empty = (await autosend.run(session, NullTransport(), mode, now=_now())).state

        assert sent.startswith("ушло 1")
        assert empty == autosend.EMPTY
        assert mode.stopped_at is not None
        assert (mode.stopped_by_id, mode.stop_reason) == (None, autosend.EMPTY)
        assert await autosend.running(session, Stage.DONORS) is None

    async def test_the_platform_refusing_everything_stops_the_pass_not_the_mode(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 2)
        mode = await _started(session)

        said = (await autosend.run(session, RefusingAll(), mode, now=_now())).state

        assert "почта отказала любому письму" in said
        assert mode.stopped_at is None
        assert await _statuses(session) == [MessageStatus.QUEUED, MessageStatus.QUEUED]

    async def test_an_unknown_outcome_holds_the_next_pass(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Почта не ответила на письмо прохода: оно «отправляется», и следующий проход не шлёт
        ничего, пока исход не решится, — даже раньше блока «Исход неизвестен»."""
        await _queue(session, 2)
        mode = await _started(session)

        broken = (await autosend.run(session, Silent(), mode, now=_now())).state
        held = (await autosend.run(session, NullTransport(), mode, now=_now())).state

        assert "связь с почтой оборвалась" in broken
        assert mode.unsure_id is not None
        assert held.startswith(f"проход стоит: исход письма №{mode.unsure_id} неизвестен")
        assert (await _statuses(session)).count(MessageStatus.SENT) == 0

    async def test_a_spent_cap_says_the_rest_goes_tomorrow_by_itself(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 3, cap=1)
        mode = await _started(session)

        await autosend.run(session, NullTransport(), mode, now=_now())
        said = (
            await autosend.run(session, NullTransport(), mode, now=_now() + autosend.gap() * 1.6)
        ).state

        assert said.startswith("Сегодня писать некому")
        assert f"{batch.BY_ITSELF}: продолжит завтра, когда лимит откроется" in said
        assert "нажмите" not in said

    async def test_the_letter_is_journaled_by_who_started_the_mode(
        self, session: AsyncSession, filled_legal: None, make_user: MakeUser
    ) -> None:
        await _queue(session, 1)
        user = await make_user("запустил@site.com", role=UserRole.ADMIN)
        mode = await _started(session, by=user.id)

        await autosend.run(session, NullTransport(), mode, now=_now())
        await session.commit()

        authors = await session.scalars(
            select(AuditLogModel.user_id).where(AuditLogModel.action == AuditAction.LETTER_SENT)
        )
        assert list(authors.all()) == [user.id]


class TestTheMode:
    async def test_one_running_launch_per_queue(self, session: AsyncSession) -> None:
        first, fresh = await autosend.start(session, Stage.DONORS, "links", by=None, now=_now())
        same, again = await autosend.start(session, Stage.DONORS, "links", by=None, now=_now())
        niche, other = await autosend.start(
            session, Stage.ADVERTISERS, "niche", by=None, now=_now()
        )

        assert (fresh, again, other) == (True, False, True)
        assert same.id == first.id
        assert niche.id != first.id

    async def test_the_state_names_who_and_how_many(
        self, session: AsyncSession, filled_legal: None, make_user: MakeUser
    ) -> None:
        await _queue(session, 2)
        user = await make_user("запустил@site.com", role=UserRole.ADMIN)
        mode = await _started(session, by=user.id)
        await autosend.run(session, NullTransport(), mode, now=_now())
        await session.commit()

        running = await autosend.state(session, Stage.DONORS)
        autosend.finish(mode, by=user.id, reason=autosend.BY_HUMAN, now=_now())
        await session.commit()
        stopped = await autosend.state(session, Stage.DONORS)

        assert (running.available, running.started_by, running.sent) == (
            True,
            "запустил@site.com",
            1,
        )
        assert running.mode is not None
        assert running.mode.state is not None
        assert stopped.mode is not None
        assert (stopped.stopped_by, stopped.mode.stop_reason) == (
            "запустил@site.com",
            autosend.BY_HUMAN,
        )

    async def test_sales_and_the_switched_off_service_have_no_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert autosend.available(Stage.DONORS)
        assert not autosend.available(Stage.SALES)
        monkeypatch.setattr(outreach_cfg, "AUTOSEND", False)
        assert not autosend.available(Stage.ADVERTISERS)


class TestTheBatchWords:
    async def test_the_button_batch_of_a_running_queue_does_not_ask_to_press_again(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 3, cap=1)

        report = await batch.send_queue(
            session, NullTransport(), stage=Stage.DONORS, automatic=True
        )

        assert report.stopped is not None
        assert batch.BY_ITSELF in report.stopped
        assert batch.BY_HAND not in report.stopped


class TestTheWorkerPass:
    def _wired(self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сессии прохода — на соединении теста, каждая в своей точке сохранения: на бою у
        каждой своё соединение, и откат упавшего запуска не трогает чужие данные."""
        monkeypatch.setattr(send_jobs, "create_async_engine", lambda _dsn: _Closable())
        monkeypatch.setattr(
            send_jobs,
            "async_sessionmaker",
            lambda _engine, **_kw: async_sessionmaker(
                bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ),
        )
        monkeypatch.setattr(send_jobs, "Transports", NullTransport)

    async def test_the_pass_sends_every_running_queue_and_commits(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _queue(session, 1)
        await _started(session)
        self._wired(session, monkeypatch)

        await send_jobs.autosend_pass()

        session.expire_all()
        assert await _statuses(session) == [MessageStatus.SENT]
        passed = await session.scalar(select(QueueAutosendModel.state))
        assert passed is not None
        assert passed.startswith("ушло 1")

    async def test_a_failing_launch_does_not_hold_the_next_one(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Первый запуск падает каждый проход — второй всё равно проходит: сбой одного — откат
        его сессии и строка журнала, а не конец прохода (ревью #505)."""
        await _queue(session, 1)
        broken, _ = await autosend.start(session, Stage.ADVERTISERS, "links", by=None, now=_now())
        await session.commit()
        working = await _started(session)
        broken_id, working_id = broken.id, working.id
        assert broken_id < working_id
        self._wired(session, monkeypatch)
        real = autosend.run

        async def run(
            run_session: AsyncSession,
            transports: object,
            mode: QueueAutosendModel,
            *,
            now: datetime,
        ) -> str:
            if mode.id == broken_id:
                raise RuntimeError("сбой первого запуска")
            return await real(run_session, transports, mode, now=now)  # type: ignore[arg-type]

        monkeypatch.setattr(autosend, "run", run)

        await send_jobs.autosend_pass()

        session.expire_all()
        assert await _statuses(session) == [MessageStatus.SENT]
        rows = await session.execute(select(QueueAutosendModel.id, QueueAutosendModel.state))
        states = dict(list(rows.tuples()))
        assert states[broken_id] is None
        assert (states[working_id] or "").startswith("ушло 1")

    async def test_the_switched_off_service_sends_nothing(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _queue(session, 1)
        await _started(session)
        self._wired(session, monkeypatch)
        monkeypatch.setattr(outreach_cfg, "AUTOSEND", False)

        await send_jobs.autosend_pass()

        session.expire_all()
        assert await _statuses(session) == [MessageStatus.QUEUED]
        assert await session.scalar(select(func.count()).select_from(QueueAutosendModel)) == 1


def test_the_pause_of_a_box_stays_within_the_spread() -> None:
    """Разброс у каждого письма свой и один на всех проходах."""
    pauses = {autosend.pause_of(1, letter) for letter in range(50)}

    assert len(pauses) > 1
    assert all(autosend.gap() * 0.5 <= pause <= autosend.gap() * 1.5 for pause in pauses)
    assert autosend.pause_of(1, 7) == autosend.pause_of(1, 7)


#: Среда, 12:00 UTC: в Нью-Йорке 07:00 — до окна, в Берлине 13:00 — в окне.
WEDNESDAY_NOON = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _office_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно по умолчанию: пн–пт 09:00–18:00 по часам получателя."""
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(5)))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time(9))
    monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time(18))


async def _donor_in(session: AsyncSession, country: str | None) -> None:
    await session.execute(update(DonorModel).values(geo=country))
    await session.commit()


class TestTheWindow:
    async def test_the_pass_waits_for_the_recipients_office_hours(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Донор из США: в 12:00 UTC у него 07:00 — проход ждёт и говорит, когда откроется окно;
        в 15:00 UTC у него 10:00 — письмо уходит. Кнопка окна не ждёт — она здесь не участвует."""
        _office_hours(monkeypatch)
        await _queue(session, 1)
        await _donor_in(session, "us")
        mode = await _started(session)

        early = await autosend.run(session, NullTransport(), mode, now=WEDNESDAY_NOON)
        statuses = await _statuses(session)
        late = await autosend.run(
            session, NullTransport(), mode, now=WEDNESDAY_NOON + timedelta(hours=3)
        )

        assert early.state == (
            "ждёт рабочего времени получателей — ближайшее откроется в 09:00 по их времени "
            "(ср 04.11, America/New_York)"
        )
        assert early.stall is None
        assert statuses == [MessageStatus.QUEUED]
        assert late.state.startswith("ушло 1")

    async def test_unknown_country_falls_back_to_the_spare_zone(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letters = await _queue(session, 1)
        await _donor_in(session, None)

        unknown = await autosend_window.zones(session, [letters[0].id])
        await _donor_in(session, "bg")
        known = await autosend_window.zones(session, [letters[0].id])

        assert unknown[letters[0].id].key == outreach_cfg.AUTOSEND_ZONE
        assert known[letters[0].id].key == "Europe/Sofia"


def test_the_country_order_of_each_audience() -> None:
    """Донор — страна его трафика, затем рынок прогона; рекламодатель по ссылке — страна донора, где
    нашли ссылку, затем рынок прогона; бизнес ниши — рынок прогона, затем страна донора-примера."""
    places = autosend_window._Places(
        traffic={1: "us"},
        found={2: ("donor.example.test", 7)},
        hosts={"donor.example.test": "es"},
        markets={5: "de", 7: "it"},
    )

    assert places.countries(Stage.DONORS, "links", 1, 5) == ["us", "de"]
    assert places.countries(Stage.ADVERTISERS, "links", 2, None) == ["es", "it"]
    assert places.countries(Stage.ADVERTISERS, "niche", 2, None) == ["it", "es"]


class TestTheStall:
    async def test_refusing_everything_and_an_unknown_outcome_stall_the_pass(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 2)
        mode = await _started(session)

        refused = await autosend.run(session, RefusingAll(), mode, now=_now())
        broken = await autosend.run(session, Silent(), mode, now=_now())
        held = await autosend.run(session, NullTransport(), mode, now=_now())

        assert refused.stall is not None
        assert broken.stall is not None
        assert held.stall == held.state

    async def test_a_spent_cap_and_resting_boxes_are_no_stall(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 3, cap=1)
        mode = await _started(session)

        sent = await autosend.run(session, NullTransport(), mode, now=_now())
        resting = await autosend.run(session, NullTransport(), mode, now=_now())
        spent = await autosend.run(
            session, NullTransport(), mode, now=_now() + autosend.gap() * 1.6
        )

        assert (sent.stall, resting.stall, spent.stall) == (None, None, None)


class TestTheAlarm:
    @pytest.fixture
    def said(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        told: list[str] = []

        async def alert(text: str) -> bool:
            told.append(text)
            return True

        monkeypatch.setattr(autosend_alarm, "send_alert", alert)
        return told

    async def test_one_alarm_after_fifteen_minutes_and_one_all_clear(self, said: list[str]) -> None:
        watch = autosend_alarm.Watch()
        stall = "почта отказала любому письму — ключ платформы не принят (401)"
        title = autosend_alarm.title_of(Stage.DONORS, "links")

        for minutes in (0, 10, 15, 20):
            await watch.see(1, title, stall, WEDNESDAY_NOON + timedelta(minutes=minutes))
        for minutes in (21, 22):
            await watch.see(1, title, None, WEDNESDAY_NOON + timedelta(minutes=minutes))

        assert said == [
            f"тревога: очередь писем донорам не уходит сама дольше 15 минут — {stall}",
            f"прошло: очередь писем донорам снова уходит сама (стояла: {stall})",
        ]

    async def test_a_pass_by_design_restarts_the_count(self, said: list[str]) -> None:
        """Ушло письмо или ящики на паузе — счёт сначала: тревоги нет и через 20 минут с первой
        остановки."""
        watch = autosend_alarm.Watch()
        title = autosend_alarm.title_of(Stage.ADVERTISERS, "niche")

        await watch.see(1, title, "исход неизвестен", WEDNESDAY_NOON)
        await watch.see(1, title, None, WEDNESDAY_NOON + timedelta(minutes=10))
        for minutes in (12, 20):
            await watch.see(
                1, title, "исход неизвестен", WEDNESDAY_NOON + timedelta(minutes=minutes)
            )

        assert said == []
        assert title == "очередь писем бизнесам ниши"

    async def test_the_worker_tells_the_alarm_a_failing_pass(
        self,
        session: AsyncSession,
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
        said: list[str],
    ) -> None:
        await _queue(session, 1)
        mode = await _started(session)
        mode_id = mode.id
        TestTheWorkerPass._wired(TestTheWorkerPass(), session, monkeypatch)
        seen: list[tuple[int, str | None]] = []

        class Seen(autosend_alarm.Watch):
            async def see(self, launch: int, title: str, stall: str | None, now: datetime) -> None:
                seen.append((launch, stall))

        async def boom(*_args: object, **_kw: object) -> autosend.Passed:
            raise RuntimeError("база не ответила")

        monkeypatch.setattr(autosend_alarm, "WATCH", Seen())
        monkeypatch.setattr(autosend, "run", boom)

        await send_jobs.autosend_pass()

        assert seen == [(mode_id, "проход падает — база не ответила")]


class TestTheForecast:
    async def test_a_self_sending_queue_is_forecast_by_pace_and_window(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _queue(session, 3)

        by_hand = await forecast.forecast(session, Stage.DONORS, days=7, back=0)
        await _started(session)
        itself = await forecast.forecast(session, Stage.DONORS, days=7, back=0)

        assert by_hand.clock.pace is None
        assert "вручную" in by_hand.eta.words
        assert itself.clock.pace == autosend.pace()
        assert itself.clock.window == autosend_window.frame()
        assert "автоотправка, письмо с ящика раз в 10 мин" in itself.eta.words


MIGRATION = "c4e1a7b20d93_queue_autosend.py"


def _down_and_up(connection: Connection) -> tuple[bool, bool, set[str]]:
    """Ревизия вниз и вверх на соединении теста: подъём сьюта идёт по моделям, и покрытие файла
    ревизии иначе не видит. Откат снимает таблицу, подъём ставит её с частным уникальным индексом
    идущего запуска."""

    def present() -> bool:
        return bool(connection.scalar(text("SELECT to_regclass('queue_autosend') IS NOT NULL")))

    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        migration.upgrade()
    indexes = connection.scalars(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'queue_autosend'")
    )
    return down, present(), set(indexes)


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up, indexes = await connection.run_sync(_down_and_up)

    assert (down, up) == (False, True)
    assert "uq_queue_autosend_running" in indexes
