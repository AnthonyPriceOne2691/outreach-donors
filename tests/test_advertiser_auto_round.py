"""Автокруг рекламодателей: перевод, поиск адресов, сборка и «уходит сама» — без трёх кнопок.

Проверяется то, чем автомат может навредить без человека у экрана: начать при выключенном
выключателе; пройти дважды за сутки; не дождаться шага судьи; собрать больше суточного потолка;
отправить очередь пачкой; включить режим у пустой очереди или второй раз у идущей; молча встать на
потолке модели; бесконечно ставить упавшую сборку; продолжить после того, как его выключили;
промолчать, когда круг встал или не начался. Модель, поиск адресов и почта не зовутся: очередь задач
— подставная, итоги задач — как их отдают задачи.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import auto_round as cfg
from backend.features.core import usage
from backend.features.core.domain import AuditAction, ContactSource, MessageStatus, Stage, Verdict
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertiser import (
    AutoRoundModel,
    AutoRoundSwitchModel,
    JudgeNightModel,
)
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlNightModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, QueueAutosendModel
from backend.features.crawl import (
    auto_round,
    auto_round_alarm,
    auto_round_switch,
    backlog,
    judge_bands,
    judge_night,
)
from backend.features.crawl.auto_round_queue import BUILD_JOB_ID, Asked
from backend.features.crawl.auto_round_report import (
    ALREADY,
    EMPTY,
    MISSED_WHY,
    OFF_MIDWAY,
    SEARCH_WAIT,
    SENDING,
    Phase,
    RoundReport,
    campaign_name,
)
from backend.features.crawl.auto_round_switch import DEFAULT_LETTERS, LettersOutOfRangeError
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_night_queue import PassSeen
from backend.features.crawl.night import NightStatus
from backend.features.letters import autosend, batch
from sqlalchemy import select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.migration_helpers import load_migration
from tests.stage2_rows import addressed, crawled, domain_of, lettered, scored
from tests.test_alarm_concern import _plain

MIGRATION = "43f1b16b7317_advertiser_auto_round.py"
TABLES = ("advertiser_auto_round_switches", "advertiser_auto_rounds")

#: Утро по Москве после ночного шага судьи: 08:00 5 ноября.
DAY = date(2026, 11, 5)
MORNING = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)
#: Включили задолго до этой даты: круг ей должен.
LONG_AGO = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


class _Killed(BaseException):
    """Процесс убит посреди круга (выкатка): `except Exception` этого не ловит."""


class FakeRound:
    """Подставная очередь задач: поиск и сборка «идут», пока тест не скажет, чем они кончились."""

    def __init__(self) -> None:
        self.searches: list[int] = []
        self.builds: list[tuple[str, int]] = []
        self.search_state = PassSeen("idle")
        self.build_state = PassSeen("idle")
        self.answer: Asked = "asked"
        self.searching: bool | None = True

    def search(self, limit: int) -> bool | None:
        if self.searching is None:
            return None
        self.searches.append(limit)
        self.search_state = PassSeen("going")
        return True

    def search_seen(self) -> PassSeen:
        return self.search_state

    def build(self, campaign: str, limit: int) -> Asked:
        if self.answer == "asked":
            self.builds.append((campaign, limit))
            self.build_state = PassSeen("going")
        return self.answer

    def build_seen(self) -> PassSeen:
        return self.build_state

    def built(self, at: datetime, prepared: int, waiting: int, **more: Any) -> None:
        said = {"prepared": prepared, "funnel": {"ещё не писали": waiting}, **more}
        self.build_state = PassSeen("done", said, at)


@pytest.fixture
def queue() -> FakeRound:
    return FakeRound()


@pytest.fixture(autouse=True)
def no_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пачку круг не шлёт никогда: позови он её — тест упадёт."""

    async def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("автокруг отдал очередь пачкой")

    monkeypatch.setattr(batch, "send_queue", never)


async def _tick(session: AsyncSession, queue: FakeRound, now: datetime = MORNING) -> None:
    """Проход круга своей сессией на точках сохранения: фиксация и откат — как у процесса разбора."""
    await session.commit()
    own = async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with own() as stepping:
        await auto_round.tick(stepping, now=now, queue=queue)


async def _on(
    session: AsyncSession, *, letters: int = DEFAULT_LETTERS, by: int | None = None
) -> None:
    await auto_round_switch.set_switch(session, on=True, letters=letters, by=by)
    await session.commit()


async def _since(session: AsyncSession, at: datetime) -> None:
    """Выключатель стоит включённым с минуты `at`."""
    await session.execute(update(AutoRoundSwitchModel).values(set_at=at))
    await session.commit()


async def _round(session: AsyncSession, day: date = DAY) -> AutoRoundModel | None:
    found = await session.scalars(
        select(AutoRoundModel)
        .where(AutoRoundModel.day == day)
        .execution_options(populate_existing=True)
    )
    return found.one_or_none()


async def _report(session: AsyncSession, day: date = DAY) -> RoundReport:
    row = await _round(session, day)
    assert row is not None
    return RoundReport.of(row.report)


async def _bought(session: AsyncSession, *hosts: str) -> None:
    """Кандидаты «куплено» — их переведёт круг."""
    run = await crawled(session, "donor.example.com")
    for host in hosts:
        await scored(session, run, host, verdict=Verdict.BOUGHT)
    await session.commit()


async def _journal(session: AsyncSession, action: AuditAction) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == action).order_by(AuditLogModel.id)
    )
    return list(rows.all())


async def _modes(session: AsyncSession) -> list[QueueAutosendModel]:
    rows = await session.scalars(
        select(QueueAutosendModel)
        .order_by(QueueAutosendModel.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


async def _judged(session: AsyncSession, status: str) -> JudgeNightModel:
    """Строка ночного шага судьи этой даты; полоса включена — шаг ждут."""
    await judge_bands.set_band(session, Band.WRITE, on=True, by=None)
    night = JudgeNightModel(night=DAY, status=status, started_at=MORNING)
    session.add(night)
    await session.commit()
    return night


# --- выключатель -----------------------------------------------------------------------------------


async def test_the_switch_is_off_until_turned_on_and_every_change_is_a_row(
    session: AsyncSession,
) -> None:
    assert await auto_round_switch.state(session) == auto_round_switch.Switch()

    was, now = await auto_round_switch.set_switch(session, on=True, letters=100, by=None)
    again = await auto_round_switch.set_switch(session, on=True, letters=100, by=None)
    _, capped = await auto_round_switch.set_switch(session, on=True, letters=40, by=None)

    assert (was.enabled, now.enabled, now.letters) == (False, True, 100)
    assert again == (now, now), "та же настройка второй раз строки не заводит"
    assert (capped.enabled, capped.letters) == (True, 40)
    assert capped.on_since == now.on_since, "смена потолка — не перерыв во включении"
    _, off = await auto_round_switch.set_switch(session, on=False, letters=40, by=None)
    assert (off.enabled, off.on_since) == (False, None)
    rows = await session.scalars(
        select(AutoRoundSwitchModel.enabled).order_by(AutoRoundSwitchModel.id)
    )
    assert list(rows.all()) == [True, True, False]


@pytest.mark.parametrize("letters", [0, 501])
async def test_the_daily_cap_stays_within_what_a_build_takes(
    session: AsyncSession, letters: int
) -> None:
    with pytest.raises(LettersOutOfRangeError, match="от 1 до 500"):
        await auto_round_switch.set_switch(session, on=True, letters=letters, by=None)


# --- когда круг начинается -------------------------------------------------------------------------


async def test_an_off_switch_does_nothing_and_spends_nothing(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "bought.example.com")

    await _tick(session, queue)

    assert await _round(session) is None
    assert (queue.searches, queue.builds) == ([], [])
    assert await session.scalar(select(AdvertiserModel.id)) is None


async def test_the_service_setting_off_holds_even_a_switched_on_round(
    session: AsyncSession, queue: FakeRound, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _bought(session, "bought.example.com")
    await _on(session)
    monkeypatch.setattr(cfg, "ENABLED", False)

    await _tick(session, queue)

    assert await _round(session) is None
    assert await session.scalar(select(AdvertiserModel.id)) is None


async def test_the_round_waits_for_the_judges_night_step(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "bought.example.com")
    await _on(session)
    night = await _judged(session, "applying")

    await _tick(session, queue)
    assert await _round(session) is None, "судья ещё ставит «пишем» — переводить рано"

    night.status = "done"
    await session.commit()
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.SEARCHING.value
    assert queue.searches == [1]


async def test_a_judge_step_that_never_ends_holds_the_round_only_until_noon(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    await _judged(session, "judging")

    await _tick(session, queue, datetime(2026, 11, 5, 8, 59, tzinfo=UTC))
    assert await _round(session) is None

    await _tick(session, queue, datetime(2026, 11, 5, 9, 0, tzinfo=UTC))
    assert await _round(session) is not None, "12:00 по Москве — круг идёт без шага судьи"


async def test_without_judge_bands_the_round_waits_for_the_crawl_round(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    crawl = CrawlNightModel(night=DAY, status=NightStatus.RUNNING.value)
    session.add(crawl)
    await session.commit()

    await _tick(session, queue, datetime(2026, 11, 5, 3, 59, tzinfo=UTC))
    await _tick(session, queue)
    assert await _round(session) is None, "до семи по Москве и пока идёт круг обходов — рано"

    crawl.status = NightStatus.DONE.value
    await session.commit()
    await _tick(session, queue)

    assert await _round(session) is not None


async def test_one_round_a_day_and_the_next_one_tomorrow(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)

    await _tick(session, queue)
    queue.built(MORNING + timedelta(minutes=5), prepared=0, waiting=0)
    await _tick(session, queue, MORNING + timedelta(minutes=6))
    await _tick(session, queue, MORNING + timedelta(hours=8))

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.DONE.value
    assert len(queue.builds) == 1, "сборка за сутки — одна"

    queue.build_state = PassSeen("idle")
    await _tick(session, queue, MORNING + timedelta(days=1))
    assert await _round(session, DAY + timedelta(days=1)) is not None


# --- круг по шагам ---------------------------------------------------------------------------------


async def test_the_round_promotes_waits_for_addresses_builds_under_the_cap_and_lets_it_go(
    session: AsyncSession, queue: FakeRound, make_user: Any
) -> None:
    owner = await make_user("владелец@example.com")
    await _bought(session, "one.example.com", "two.example.com")
    await _on(session, letters=30, by=owner.id)

    await _tick(session, queue)

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.SEARCHING.value
    assert queue.searches == [2], "поиск — тем, кто ждёт адреса, той же задачей, что у кнопки"
    (promotion,) = await _journal(session, AuditAction.ADVERTISER_REVIEWED)
    assert promotion.user_id is None
    assert promotion.target == "advertisers:promote"
    assert promotion.details is not None
    assert promotion.details["кто"] == "автокруг"
    assert promotion.details["выключатель включил"] == "владелец@example.com"
    assert promotion.details["заведено"] == 2

    await _tick(session, queue, MORNING + timedelta(minutes=1))
    assert queue.builds == [], "поиск идёт — собирать рано"

    found = await addressed(session, "one.example.com")
    found.source = ContactSource.PROVIDER
    await session.commit()
    queue.search_state = PassSeen("done", {"saved": 1}, MORNING + timedelta(minutes=20))
    await _tick(session, queue, MORNING + timedelta(minutes=21))

    assert queue.builds == [("Автокруг 05.11 — рекламодатели по ссылке", 30)]
    (build,) = [
        one
        for one in await _journal(session, AuditAction.RUN_STARTED)
        if one.target == f"job:{BUILD_JOB_ID}"
    ]
    assert build.details is not None
    assert (build.details["писем"], build.details["кто"]) == (30, "автокруг")

    await lettered(session, "one.example.com", MessageStatus.QUEUED)
    await session.commit()
    queue.built(MORNING + timedelta(minutes=40), prepared=1, waiting=1)
    await _tick(session, queue, MORNING + timedelta(minutes=41))

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.DONE.value
    report = RoundReport.of(row.report)
    assert (report.promoted, report.found, report.paid) == (2, 1, 1)
    assert (report.prepared, report.waiting, report.queue) == (1, 1, SENDING)
    (mode,) = await _modes(session)
    assert (mode.stage, mode.audience, mode.stopped_at) == (Stage.ADVERTISERS, "links", None)
    assert mode.started_by_id == owner.id, "письма уходят от имени включившего выключатель"
    started = [
        one
        for one in await _journal(session, AuditAction.RUN_STARTED)
        if one.target == f"queue_autosend:{mode.id}"
    ]
    assert len(started) == 1


async def test_nothing_to_search_goes_straight_to_the_build(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)

    await _tick(session, queue)

    assert queue.searches == []
    assert len(queue.builds) == 1, "ждущих адреса нет — сборка в том же проходе"


async def test_a_search_that_does_not_end_in_time_does_not_hold_the_build(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "slow.example.com")
    await _on(session)
    await _tick(session, queue)

    await _tick(session, queue, MORNING + SEARCH_WAIT - timedelta(minutes=1))
    assert queue.builds == []

    await _tick(session, queue, MORNING + SEARCH_WAIT)

    assert len(queue.builds) == 1
    assert (await _report(session)).search_cut is True


async def test_a_silent_queue_asks_the_search_again_next_pass(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "silent.example.com")
    await _on(session)
    queue.searching = None

    await _tick(session, queue)
    row = await _round(session)
    assert row is not None
    assert (row.status, row.asked_at) == (Phase.SEARCHING.value, None)

    queue.searching = True
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    assert queue.searches == [1]


async def test_a_running_queue_is_not_started_again(
    session: AsyncSession, queue: FakeRound
) -> None:
    """Режим уже идёт — новые письма он подхватит сам: второй запуск круг не заводит."""
    await _on(session)
    await lettered(session, "queued.example.com", MessageStatus.QUEUED)
    await autosend.start(session, Stage.ADVERTISERS, "links", by=None, now=MORNING)
    await session.commit()

    await _tick(session, queue)
    queue.built(MORNING + timedelta(minutes=5), prepared=0, waiting=0)
    await _tick(session, queue, MORNING + timedelta(minutes=6))

    assert len(await _modes(session)) == 1
    assert (await _report(session)).queue == ALREADY


async def test_an_empty_queue_does_not_turn_the_mode_on(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)

    await _tick(session, queue)
    queue.built(MORNING + timedelta(minutes=5), prepared=0, waiting=0)
    await _tick(session, queue, MORNING + timedelta(minutes=6))

    assert await _modes(session) == []
    assert (await _report(session)).queue == EMPTY


async def test_the_model_cap_before_the_build_is_said_and_not_a_failure(
    session: AsyncSession, queue: FakeRound, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def spent(*_args: object, **_kwargs: object) -> None:
        raise usage.LlmCapExceededError("дневной потолок модели выбран")

    monkeypatch.setattr(usage, "ensure_llm_within_cap", spent)
    await _on(session)

    await _tick(session, queue)

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.DONE.value
    assert queue.builds == []
    report = RoundReport.of(row.report)
    assert (report.stopped, report.queue) == ("дневной потолок модели выбран", EMPTY)


async def test_a_build_stopped_by_the_cap_midway_keeps_what_it_built(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    await _tick(session, queue)
    queue.built(
        MORNING + timedelta(minutes=5), prepared=3, waiting=9, stopped="потолок модели выбран"
    )

    await _tick(session, queue, MORNING + timedelta(minutes=6))

    report = await _report(session)
    assert (report.prepared, report.waiting, report.stopped) == (3, 9, "потолок модели выбран")


async def test_a_build_started_by_a_human_is_waited_for(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    queue.answer = "busy"

    await _tick(session, queue)
    row = await _round(session)
    assert row is not None
    assert (row.status, row.asked_at, row.tries) == (Phase.BUILDING.value, None, 0)

    queue.answer = "asked"
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    row = await _round(session)
    assert row is not None
    assert (row.asked_at, row.tries) == (MORNING + timedelta(minutes=1), 1)


async def test_a_failing_build_is_asked_again_but_not_forever(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    minute = MORNING
    for _ in range(auto_round.MAX_TRIES):
        await _tick(session, queue, minute)
        queue.build_state = PassSeen("failed", error="шаблон не разбирается")
        minute += timedelta(minutes=1)
        await _tick(session, queue, minute)
        minute += timedelta(minutes=1)

    await _tick(session, queue, minute)

    assert len(queue.builds) == auto_round.MAX_TRIES
    row = await _round(session)
    assert row is not None
    assert row.status == Phase.FAILED.value
    assert row.note is not None
    assert "шаблон не разбирается" in row.note
    found = await auto_round_alarm.alarm(session, minute)
    assert found is not None
    assert found.stage is Stage.ADVERTISERS
    assert found.concern is not None
    _plain(found.concern)


async def test_a_retry_builds_only_what_is_left_of_the_daily_cap(
    session: AsyncSession, queue: FakeRound
) -> None:
    """Сборка упала, успев собрать два письма: повтор собирает остаток потолка, а итог считает
    письма обеих попыток."""
    await _on(session, letters=5)
    session.add(CampaignModel(name=campaign_name(DAY), stage=Stage.ADVERTISERS, status="draft"))
    await _tick(session, queue)
    for host in ("first.example.com", "second.example.com"):
        await lettered(session, host, MessageStatus.QUEUED)
    queue.build_state = PassSeen("failed", error="воркер перезапустился посреди задачи")
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    await _tick(session, queue, MORNING + timedelta(minutes=2))
    queue.built(MORNING + timedelta(minutes=5), prepared=3, waiting=4)
    await _tick(session, queue, MORNING + timedelta(minutes=6))

    assert [limit for _, limit in queue.builds] == [5, 3]
    report = await _report(session)
    assert (report.prepared, report.waiting) == (5, 6)


async def test_a_cap_filled_by_earlier_tries_ends_the_round_without_another_build(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session, letters=2)
    session.add(CampaignModel(name=campaign_name(DAY), stage=Stage.ADVERTISERS, status="draft"))
    await _tick(session, queue)
    for host in ("first.example.com", "second.example.com"):
        await lettered(session, host, MessageStatus.QUEUED)
    queue.build_state = PassSeen("failed", error="воркер перезапустился посреди задачи")
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    await _tick(session, queue, MORNING + timedelta(minutes=2))

    assert len(queue.builds) == 1
    row = await _round(session)
    assert row is not None
    assert row.status == Phase.DONE.value
    report = RoundReport.of(row.report)
    assert (report.prepared, report.queue) == (2, SENDING)


async def test_the_search_is_no_bigger_than_the_daily_cap(
    session: AsyncSession, queue: FakeRound
) -> None:
    """Платная ступень тратится на тех, кому круг успеет написать: остальные — следующим кругом."""
    await _bought(session, "a.example.com", "b.example.com", "c.example.com")
    await _on(session, letters=2)

    await _tick(session, queue)

    assert queue.searches == [2]


async def test_a_mode_turned_on_by_a_human_in_the_same_second_is_not_a_failure(
    session: AsyncSession, queue: FakeRound, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raced(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError(
            "INSERT INTO queue_autosend", {}, Exception("uq_queue_autosend_running")
        )

    monkeypatch.setattr(auto_round.autosend, "start", raced)
    await _on(session)
    await lettered(session, "queued.example.com", MessageStatus.QUEUED)
    await _tick(session, queue)
    queue.built(MORNING + timedelta(minutes=5), prepared=0, waiting=0)

    await _tick(session, queue, MORNING + timedelta(minutes=6))

    row = await _round(session)
    assert row is not None
    assert (row.status, RoundReport.of(row.report).queue) == (Phase.DONE.value, ALREADY)


async def test_a_switch_turned_off_midway_stops_the_round(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "midway.example.com")
    await _on(session)
    await _tick(session, queue)
    await auto_round_switch.set_switch(session, on=False, letters=DEFAULT_LETTERS, by=None)
    await session.commit()

    await _tick(session, queue, MORNING + timedelta(minutes=1))
    queue.search_state = PassSeen("done", {}, MORNING + timedelta(minutes=2))
    await _tick(session, queue, MORNING + timedelta(minutes=3))

    row = await _round(session)
    assert row is not None
    assert (row.status, row.note) == (Phase.STOPPED.value, OFF_MIDWAY)
    assert queue.builds == []
    assert await auto_round_alarm.alarm(session, MORNING + timedelta(minutes=3)) is None


async def test_a_killed_pass_leaves_nothing_half_written(
    session: AsyncSession, queue: FakeRound
) -> None:
    """Выкатка посреди постановки сборки: строка круга не записала её — следующий проход ставит."""
    await _on(session)

    def killed(_campaign: str, _limit: int) -> Asked:
        raise _Killed

    real = queue.build
    queue.build = killed  # type: ignore[method-assign]
    with pytest.raises(_Killed):
        await _tick(session, queue)
    queue.build = real  # type: ignore[method-assign]

    await _tick(session, queue, MORNING + timedelta(minutes=1))

    row = await _round(session)
    assert row is not None
    assert (row.tries, len(queue.builds)) == (1, 1)


async def test_a_crash_is_written_into_the_round_and_alarmed(
    session: AsyncSession, queue: FakeRound, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_session: AsyncSession) -> None:
        raise RuntimeError("база отвалилась посреди перевода")

    monkeypatch.setattr(auto_round.promote, "promote", broken)
    await _on(session)

    await _tick(session, queue)

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.FAILED.value
    assert row.note == "круг упал: база отвалилась посреди перевода"
    found = await auto_round_alarm.alarm(session, MORNING + timedelta(minutes=1))
    assert found is not None
    assert found.title == "Автокруг рекламодателей встал"


# --- пропущенные даты и сторож ---------------------------------------------------------------------


async def test_dates_without_a_round_are_written_missed_once(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    await _since(session, LONG_AGO)
    evening = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)  # 20:30 по Москве

    await _tick(session, queue, evening)
    await _tick(session, queue, evening + timedelta(minutes=1))

    for day in (DAY - timedelta(days=1), DAY):
        row = await _round(session, day)
        assert row is not None
        assert (row.status, row.note) == (Phase.MISSED.value, MISSED_WHY)
    assert (queue.searches, queue.builds) == ([], [])
    found = await auto_round_alarm.alarm(session, evening)
    assert found is not None
    assert found.title == "Автокруг рекламодателей не начался"


async def test_a_switch_turned_on_today_owes_nothing_for_yesterday(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    await _since(session, MORNING - timedelta(minutes=30))

    await _tick(session, queue)

    assert await _round(session, DAY - timedelta(days=1)) is None
    assert await _round(session) is not None


async def test_the_watchdog_sees_a_round_that_never_started_even_without_the_pass(
    session: AsyncSession,
) -> None:
    """Проход, который ведёт круг, не работает вовсе — строки «пропущен» записать некому, а сторож
    видит по часам и выключателю."""
    await _on(session)
    await _since(session, LONG_AGO)

    assert await auto_round_alarm.alarm(session, datetime(2026, 11, 5, 16, 0, tzinfo=UTC)) is None
    found = await auto_round_alarm.alarm(session, datetime(2026, 11, 5, 17, 30, tzinfo=UTC))

    assert found is not None
    assert found.code == auto_round_alarm.ALARM


async def test_the_watchdog_sees_a_round_stuck_on_its_step(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "stuck.example.com")
    await _on(session)
    await _tick(session, queue)

    assert await auto_round_alarm.alarm(session, MORNING + timedelta(hours=7)) is None
    found = await auto_round_alarm.alarm(session, MORNING + timedelta(hours=9))

    assert found is not None
    assert found.title == "Автокруг рекламодателей стоит"
    assert "ждёт поиск адресов" in found.detail


async def test_the_watchdog_is_quiet_when_switched_off_or_when_the_round_went_fine(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _on(session)
    await _tick(session, queue)
    queue.built(MORNING + timedelta(minutes=5), prepared=0, waiting=0)
    await _tick(session, queue, MORNING + timedelta(minutes=6))

    assert await auto_round_alarm.alarm(session, MORNING + timedelta(hours=10)) is None

    row = await _round(session)
    assert row is not None
    row.status, row.note = Phase.FAILED.value, "сборка ставилась 3 раза"
    await auto_round_switch.set_switch(session, on=False, letters=DEFAULT_LETTERS, by=None)
    await session.commit()

    assert await auto_round_alarm.alarm(session, MORNING + timedelta(hours=10)) is None


async def test_addresses_found_before_the_round_are_not_the_rounds(
    session: AsyncSession, queue: FakeRound
) -> None:
    await _bought(session, "old.example.com")
    domain = await domain_of(session, "old.example.com")
    session.add(
        ContactModel(
            domain_id=domain.id,
            email="ads@old.example.com",
            source=ContactSource.PAGE,
            created_at=MORNING - timedelta(days=1),
        )
    )
    await _on(session)

    await _tick(session, queue)

    report = await _report(session)
    assert (report.promoted, report.found, report.searched) == (1, 0, False)


# --- где круг живёт и ревизия базы -----------------------------------------------------------------


async def test_the_round_ticks_in_the_backlog_pass_after_the_judges_step(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    async def judged(_session: AsyncSession, *, now: datetime | None = None) -> None:
        order.append("судья")

    async def rounded(_session: AsyncSession, *, now: datetime | None = None) -> None:
        order.append(f"автокруг {now:%H:%M}")

    def never(*_args: object) -> str:
        raise AssertionError("обходчиков нет — ставить обход некому")

    monkeypatch.setattr(judge_night, "tick", judged)
    monkeypatch.setattr(auto_round, "tick", rounded)

    await backlog.feed(session, workers=0, enqueue=never, job_id=never, now=MORNING)

    assert order == ["судья", "автокруг 05:00"]


def _tables(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)"),
        {"names": list(TABLES)},
    )
    return set(rows.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str], list[str]]:
    """Ревизия вниз и вверх на соединении теста: иначе файл ревизии впервые исполнился бы на
    выкатке."""
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _tables(connection)
        migration.upgrade()
        up = _tables(connection)
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return down, up, list(values.scalars())


async def test_the_revision_goes_down_and_up_and_the_round_runs_on_its_tables(
    session: AsyncSession, queue: FakeRound
) -> None:
    """Откат снимает обе таблицы, подъём возвращает их такими, что круг на них идёт: выключатель —
    строкой истории, дата круга — уникальной строкой (`on conflict`)."""
    connection = await session.connection()

    down, up, values = await connection.run_sync(_down_and_up)

    assert down == set()
    assert up == set(TABLES)
    assert values.count("advertiser_auto_round_changed") == 1

    await _on(session)
    await _tick(session, queue)
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    row = await _round(session)
    assert row is not None
    assert row.status == Phase.BUILDING.value
    assert len(queue.builds) == 1
