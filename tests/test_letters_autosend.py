"""Очередь, которая уходит сама (`letters/autosend.py`): замечание тест-группы 29.10.2026.

Проверяется то, на чём режим держится: проход — та же пачка, по письму с ящика и с паузой между
его первыми письмами; режим кончается сам, когда очередь пуста; отказ платформы любому письму и
неизвестный исход останавливают проход, а не режим; запуск у очереди один; письмо в журнале — с
тем, кто включил режим; итог пачки при включённом режиме не просит нажать её снова; выключенный
рубильник проходы не пускает.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import AuditAction, MessageStatus, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel, QueueAutosendModel
from backend.features.letters import autosend, batch
from backend.features.letters.transport import NullTransport
from backend.workers import send_jobs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import make_sender
from tests.test_letters_send_queue import RefusingAll, Silent, _Closable, _queue

MakeUser = Callable[..., Awaitable[UserModel]]


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

        first = await autosend.run(session, NullTransport(), mode, now=_now())
        again = await autosend.run(session, NullTransport(), mode, now=_now())
        later = await autosend.run(
            session, NullTransport(), mode, now=_now() + autosend.gap() * 1.6
        )

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

        sent = await autosend.run(session, NullTransport(), mode, now=_now())
        empty = await autosend.run(session, NullTransport(), mode, now=_now())

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

        said = await autosend.run(session, RefusingAll(), mode, now=_now())

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

        broken = await autosend.run(session, Silent(), mode, now=_now())
        held = await autosend.run(session, NullTransport(), mode, now=_now())

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
        said = await autosend.run(session, NullTransport(), mode, now=_now() + autosend.gap() * 1.6)

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
