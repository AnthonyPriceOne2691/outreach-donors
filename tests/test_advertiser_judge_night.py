"""Судья сам, каждую ночь: полосы автоприменения и шаг ночи.

Проверяется то, чем автомат может навредить без человека у экрана: начать при выключенных полосах и
потратить токены; пройти дважды за ночь; после выкатки применить те же пары второй раз; применить
выключенную полосу; молча встать на потолке модели; решить за ночь больше потолка; снять «не пишем»
заведённого рекламодателя; оставить партию, которую не откатить; шуметь, когда делать нечего; начать
до конца круга обходов. Модель не зовётся: очередь разбора — подставная, строки суда заведены как
после прохода.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import llm as llm_cfg
from backend.features.core.domain import AuditAction, Verdict
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertiser import (
    AdvertiserJudgementModel,
    CandidateModel,
    JudgeBandModel,
    JudgeNightModel,
)
from backend.features.core.models.crawl import CrawlNightModel
from backend.features.core.usage import record
from backend.features.crawl import (
    judge_apply,
    judge_bands,
    judge_batches,
    judge_night,
    judge_revert,
)
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_mark import batch_of, marker_of
from backend.features.crawl.judge_night import Phase
from backend.features.crawl.judge_night_queue import PassSeen
from backend.features.crawl.judge_pass import JUDGE_OPERATION, RESTARTING
from backend.features.crawl.judge_rules import RULES_VERSION
from backend.features.crawl.night import NIGHT_BY, NightStatus
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.migration_helpers import load_migration
from tests.stage2_rows import crawled, promoted, scored
from tests.test_advertiser_judge_apply import disputed, judged

MIGRATION = "5fcbc5aa3951_advertiser_judge_night.py"
TABLES = ("advertiser_judge_bands", "advertiser_judge_nights")

#: Утро по Москве после круга обходов: 08:00 5 ноября. В прошлом: имя партии — от этой минуты, а
#: партию «из будущего» применение не принимает.
DAY = date(2026, 11, 5)
MORNING = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)


class _Killed(BaseException):
    """Процесс убит посреди шага (выкатка): `except Exception` этого не ловит."""


class FakePass:
    """Подставная очередь разбора: разбор «идёт», пока тест не скажет, чем он кончился."""

    def __init__(self) -> None:
        self.started: list[tuple[int, str]] = []
        self.state = PassSeen("idle")
        self.answers = True

    def seen(self) -> PassSeen:
        return self.state

    def start(self, cases: int, *, by: str) -> bool | None:
        if not self.answers:
            return None
        self.started.append((cases, by))
        self.state = PassSeen("going")
        return True

    def finish(self, at: datetime, **report: Any) -> None:
        said = {"lines": ["рассужены: 1", "токенов: 4 000"], "done": 1, "total": 1, **report}
        self.state = PassSeen("done", said, at)


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги шага — в список."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(judge_night, "send_alert", send)
    return said


async def _on(session: AsyncSession, *bands: Band) -> None:
    for band in bands:
        await judge_bands.set_band(session, band, on=True, by=None)


async def _tick(session: AsyncSession, queue: FakePass, now: datetime = MORNING) -> None:
    """Проход шага своей сессией на точках сохранения: фиксация и откат — как у процесса разбора,
    незафиксированное умирает вместе с проходом."""
    await session.commit()
    own = async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with own() as stepping:
        await judge_night.tick(stepping, now=now, queue=queue)


async def _night(session: AsyncSession, day: date = DAY) -> JudgeNightModel | None:
    found = await session.scalars(
        select(JudgeNightModel)
        .where(JudgeNightModel.night == day)
        .execution_options(populate_existing=True)
    )
    return found.one_or_none()


async def _decisions(session: AsyncSession) -> dict[str, tuple[bool | None, str | None]]:
    rows = await session.execute(
        select(CandidateModel.target_root, CandidateModel.confirmed, CandidateModel.decided_by)
    )
    return {root: (confirmed, by) for root, confirmed, by in rows.tuples()}


async def _portions(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.ADVERTISER_JUDGE_APPLIED)
        .order_by(AuditLogModel.id)
    )
    return list(rows.all())


# --- полосы ----------------------------------------------------------------------------------------


async def test_bands_are_off_until_someone_turns_one_on_and_the_history_stays(
    session: AsyncSession,
) -> None:
    assert await judge_bands.enabled(session) == frozenset()

    was = await judge_bands.set_band(session, Band.SKIP, on=True, by=None)
    again = await judge_bands.set_band(session, Band.SKIP, on=True, by=None)

    assert (was, again) == (False, True)
    assert await judge_bands.enabled(session) == frozenset({Band.SKIP})
    await judge_bands.set_band(session, Band.SKIP, on=False, by=None)
    assert await judge_bands.enabled(session) == frozenset()
    history = await session.scalars(select(JudgeBandModel.enabled).order_by(JudgeBandModel.id))
    assert list(history.all()) == [True, False], "та же настройка второй раз строки не заводит"


# --- когда шаг начинается --------------------------------------------------------------------------


async def test_a_night_without_bands_does_nothing_and_spends_nothing(
    session: AsyncSession, alerts: list[str]
) -> None:
    await disputed(session, ("skip.example.test", "skip"), ("new.example.test", None))
    queue = FakePass()

    await _tick(session, queue)

    assert await _night(session) is None
    assert queue.started == []
    assert (await _decisions(session))["skip.example.test"] == (None, None)
    assert alerts == []


async def test_the_step_waits_for_the_crawl_round_to_end(session: AsyncSession) -> None:
    await disputed(session, ("skip.example.test", "skip"))
    await _on(session, Band.SKIP)
    round_ = CrawlNightModel(night=DAY, status=NightStatus.RUNNING.value)
    session.add(round_)
    queue = FakePass()

    await _tick(session, queue)
    assert await _night(session) is None, "круг ещё идёт — новые спорные ещё едут"

    round_.status = NightStatus.DONE.value
    await _tick(session, queue)

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    assert (await _decisions(session))["skip.example.test"][0] is False


async def test_without_a_round_the_step_starts_at_seven_by_moscow(session: AsyncSession) -> None:
    await disputed(session, ("skip.example.test", "skip"))
    await _on(session, Band.SKIP)
    queue = FakePass()

    await _tick(session, queue, datetime(2026, 11, 5, 3, 59, tzinfo=UTC))  # 06:59 по Москве
    assert await _night(session) is None

    await _tick(session, queue, datetime(2026, 11, 5, 4, 0, tzinfo=UTC))

    assert (await _night(session)) is not None


async def test_one_night_is_one_step(session: AsyncSession) -> None:
    """Второй проход той же даты ничего не решает, даже если появились новые пары с советом;
    следующая дата — новый шаг и новая партия."""
    ids = await disputed(session, ("first.example.test", "skip"))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue)

    run = await crawled(session, "second-donor.example.test")
    await scored(session, run, "later.example.test", verdict=Verdict.PENDING)
    session.add(judged("later.example.test", "skip", donor=run.host))
    await _tick(session, queue, MORNING + timedelta(hours=3))

    decided = await _decisions(session)
    assert decided["first.example.test"][0] is False
    assert decided["later.example.test"] == (None, None)

    await _tick(session, queue, MORNING + timedelta(days=1))

    decided = await _decisions(session)
    assert decided["later.example.test"][0] is False
    assert decided["later.example.test"][1] != decided["first.example.test"][1], "партия своя"
    assert len(ids) == 1


async def test_a_step_started_in_the_daytime_does_not_eat_the_next_night(
    session: AsyncSession,
) -> None:
    """Полосу включили вечером — шаг идёт сразу, под сегодняшней датой. Ночной шаг следующей даты
    это не отменяет: он ждёт свой круг обходов и идёт своей строкой и своей партией."""
    await disputed(session, ("evening.example.test", "skip"))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue, datetime(2026, 11, 5, 16, 30, tzinfo=UTC))  # 19:30 по Москве

    first = await _night(session)
    assert first is not None
    assert first.status == Phase.DONE.value
    assert (await _decisions(session))["evening.example.test"][0] is False

    # Ночь принесла новую спорную пару; круг обходов следующей даты ещё идёт — шаг ждёт его.
    tomorrow = DAY + timedelta(days=1)
    run = await crawled(session, "second-donor.example.test")
    await scored(session, run, "night.example.test", verdict=Verdict.PENDING)
    session.add(judged("night.example.test", "skip", donor=run.host))
    round_ = CrawlNightModel(night=tomorrow, status=NightStatus.RUNNING.value)
    session.add(round_)
    await _tick(session, queue, datetime(2026, 11, 5, 22, 0, tzinfo=UTC))  # 01:00 следующей даты

    assert await _night(session, tomorrow) is None
    assert (await _decisions(session))["night.example.test"] == (None, None)

    round_.status = NightStatus.DONE.value
    await _tick(session, queue, datetime(2026, 11, 6, 1, 0, tzinfo=UTC))  # 04:00 по Москве

    second = await _night(session, tomorrow)
    assert second is not None, "вечерний шаг ночного не съел"
    assert second.status == Phase.DONE.value
    decided = await _decisions(session)
    assert decided["night.example.test"][0] is False
    assert decided["night.example.test"][1] != decided["evening.example.test"][1], "партия своя"


async def test_a_step_that_crosses_midnight_ends_under_its_own_date_and_the_next_one_still_runs(
    session: AsyncSession,
) -> None:
    """Шаг начат поздно вечером, а разбор кончился после полуночи: шаг доходит под датой начала —
    не брошен на полпути, — и шаг новой даты начинается после него в свой час."""
    await disputed(session, ("judged.example.test", "skip"), ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    late = datetime(2026, 11, 5, 20, 30, tzinfo=UTC)  # 23:30 по Москве
    tomorrow = DAY + timedelta(days=1)
    await _tick(session, queue, late)
    await _tick(session, queue, late + timedelta(minutes=40))  # 00:10 следующей даты: разбор идёт

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.JUDGING.value
    assert await _night(session, tomorrow) is None
    assert len(queue.started) == 1

    queue.finish(late + timedelta(minutes=50))
    await _tick(session, queue, late + timedelta(minutes=51))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value, "шаг кончился под датой начала"
    assert (await _decisions(session))["judged.example.test"][0] is False
    assert await _night(session, tomorrow) is None, "шаг новой даты ждёт свой час"

    await _tick(session, queue, datetime(2026, 11, 6, 4, 30, tzinfo=UTC))  # 07:30 по Москве

    following = await _night(session, tomorrow)
    assert following is not None, "вчерашний шаг ночного не съел"
    assert len(queue.started) == 2


async def test_a_step_fallen_after_midnight_is_said_under_its_own_date(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    await disputed(session, ("judged.example.test", "skip"), ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    late = datetime(2026, 11, 5, 20, 30, tzinfo=UTC)  # 23:30 по Москве
    await _tick(session, queue, late)

    async def broken(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("пересчёт сломан")

    monkeypatch.setattr(judge_night, "relabel", broken)
    queue.finish(late + timedelta(minutes=50))
    await _tick(session, queue, late + timedelta(minutes=51))  # 00:21 следующей даты
    await _tick(session, queue, late + timedelta(minutes=52))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.FAILED.value
    assert len(alerts) == 1
    assert "05.11" in alerts[0], "тревога называет дату шага, а не дату прохода"
    assert await _night(session, DAY + timedelta(days=1)) is None


# --- разбор спорных --------------------------------------------------------------------------------


async def test_new_disputed_are_judged_first_by_the_same_job_as_the_button(
    session: AsyncSession,
) -> None:
    """Нерассуженные пары — разбор той же задачей: шаг ставит его, ждёт и только потом применяет."""
    ids = await disputed(session, ("judged.example.test", "skip"), ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()

    await _tick(session, queue)
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    night = await _night(session)
    assert night is not None
    assert (night.status, night.tries) == (Phase.JUDGING.value, 1)
    assert queue.started == [(1, NIGHT_BY)], "разбор один, сколько бы проходов ни шло"
    assert (await _decisions(session))["judged.example.test"] == (None, None)

    # Разбор кончился: новой паре — совет, как его пишет проход.
    session.add(judged("new.example.test", "skip"))
    queue.finish(MORNING + timedelta(minutes=30), advice={"skip": 1})
    await _tick(session, queue, MORNING + timedelta(minutes=31))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    assert night.report is not None
    assert night.report["advice"] == {"skip": 1}
    decided = await _decisions(session)
    assert {root for root, (confirmed, _) in decided.items() if confirmed is False} == set(ids)


async def test_a_spent_cap_stops_the_judging_in_words_and_the_judged_are_still_applied(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 100)
    await disputed(session, ("judged.example.test", "skip"), ("new.example.test", None))
    record(session, operation=JUDGE_OPERATION, units=150)
    await _on(session, Band.SKIP)
    queue = FakePass()

    await _tick(session, queue)

    night = await _night(session)
    assert night is not None
    assert queue.started == [], "потолок выбран до начала — модель не зовётся"
    assert night.status == Phase.DONE.value
    assert night.report is not None
    assert "судьи рекламодателей" in night.report["stopped"]
    decided = await _decisions(session)
    assert decided["judged.example.test"][0] is False
    assert decided["new.example.test"] == (None, None), "остаток — следующей ночью"


async def test_a_cap_hit_in_the_middle_keeps_its_words(session: AsyncSession) -> None:
    await disputed(session, ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue)

    queue.finish(MORNING + timedelta(minutes=5), stopped="дневной потолок судьи выбран", left=1)
    await _tick(session, queue, MORNING + timedelta(minutes=6))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    assert night.report is not None
    assert night.report["stopped"] == "дневной потолок судьи выбран"
    assert queue.started == [(1, NIGHT_BY)], "упёрлись в потолок — второй разбор не ставится"


async def test_a_judging_cut_by_a_deploy_is_asked_again_but_not_forever(
    session: AsyncSession, alerts: list[str]
) -> None:
    await disputed(session, ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue)

    for minute in range(1, judge_night.MAX_TRIES + 2):
        queue.finish(MORNING + timedelta(minutes=minute), stopped=RESTARTING)
        await _tick(session, queue, MORNING + timedelta(minutes=minute, seconds=30))

    night = await _night(session)
    assert night is not None
    assert len(queue.started) == judge_night.MAX_TRIES
    assert night.status == Phase.FAILED.value
    assert night.note is not None
    assert "не дошёл до конца" in night.note
    assert len(alerts) == 1, "тревога один раз, а не каждый проход"

    await _tick(session, queue, MORNING + timedelta(hours=1))
    assert len(alerts) == 1
    assert len(queue.started) == judge_night.MAX_TRIES


async def test_bands_turned_off_in_the_middle_ask_no_new_judging(session: AsyncSession) -> None:
    """Разбор оборвала выкатка, а полосы за это время выключили: второй раз шаг его не ставит —
    без включённой полосы токены не тратятся — и кончается, ничего не применив."""
    await disputed(session, ("new.example.test", None), ("skip.example.test", "skip"))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue)
    assert len(queue.started) == 1

    queue.finish(MORNING + timedelta(minutes=1), stopped=RESTARTING)
    await judge_bands.set_band(session, Band.SKIP, on=False, by=None)
    await _tick(session, queue, MORNING + timedelta(minutes=2))

    night = await _night(session)
    assert night is not None
    assert len(queue.started) == 1
    assert night.status == Phase.DONE.value
    assert night.report["stopped"] == judge_night.BANDS_OFF
    assert (await _decisions(session))["skip.example.test"] == (None, None)
    assert await _portions(session) == []


async def test_a_silent_queue_is_not_an_outcome(session: AsyncSession, alerts: list[str]) -> None:
    """Очередь не ответила — шаг ждёт следующего прохода: ни попытки, ни тревоги."""
    await disputed(session, ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    queue.answers = False

    await _tick(session, queue)

    night = await _night(session)
    assert night is not None
    assert (night.status, night.tries, night.pass_asked_at) == (Phase.JUDGING.value, 0, None)
    assert alerts == []

    queue.answers = True
    await _tick(session, queue, MORNING + timedelta(minutes=1))
    assert queue.started == [(1, NIGHT_BY)]


async def test_a_result_of_last_night_is_not_taken_for_this_one(session: AsyncSession) -> None:
    await disputed(session, ("new.example.test", None))
    await _on(session, Band.SKIP)
    queue = FakePass()
    await _tick(session, queue)

    # Итог лежит в очереди с прошлой ночи: кончился раньше, чем шаг спросил.
    queue.finish(MORNING - timedelta(hours=20))
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.JUDGING.value
    assert len(queue.started) == 2


# --- применение ------------------------------------------------------------------------------------


async def test_an_enabled_band_is_applied_and_a_disabled_one_is_not(session: AsyncSession) -> None:
    ids = await disputed(
        session,
        ("skip.example.test", "skip"),
        ("write.example.test", "write"),
        ("unclear.example.test", "unclear"),
    )
    await _on(session, Band.SKIP)

    await _tick(session, FakePass())

    decided = await _decisions(session)
    assert decided["skip.example.test"][0] is False
    assert decided["write.example.test"] == (None, None), "полоса «пишем» выключена"
    assert decided["unclear.example.test"] == (None, None)
    night = await _night(session)
    assert night is not None
    assert night.applied is not None
    assert "write" not in night.applied
    assert night.applied["skip"]["applied"] == 1
    assert len(ids) == 3


async def test_the_night_batch_is_in_the_journal_without_an_account_and_can_be_reverted(
    session: AsyncSession,
) -> None:
    await disputed(session, ("one.example.test", "skip"), ("two.example.test", "write"))
    await _on(session, Band.SKIP, Band.WRITE)

    await _tick(session, FakePass())

    decided = await _decisions(session)
    skipped, written = decided["one.example.test"], decided["two.example.test"]
    assert (skipped[0], written[0]) == (False, True)
    portions = await _portions(session)
    assert [(entry.user_id, (entry.details or {})["кто"]) for entry in portions] == [
        (None, NIGHT_BY),
        (None, NIGHT_BY),
    ]
    listed = {batch.band: batch for batch in await judge_batches.batches(session)}
    assert (listed["skip"].by, listed["skip"].applied, listed["skip"].standing) == (NIGHT_BY, 1, 1)
    assert marker_of(listed["write"].id) == written[1]

    batch = batch_of(skipped[1])
    assert batch is not None
    reverted = await judge_revert.revert(session, batch)

    assert reverted.returned == 1
    assert (await _decisions(session))["one.example.test"] == (None, None)


async def test_a_step_killed_in_the_middle_goes_on_and_applies_nothing_twice(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выкатка убила процесс посреди применения: зафиксированные порции остались, летевшая умерла,
    следующий проход продолжает ту же партию — каждая пара решена один раз."""
    targets = [f"site-{number}.example.test" for number in range(5)]
    await disputed(session, *((target, "skip") for target in targets))
    await _on(session, Band.SKIP)
    monkeypatch.setattr(judge_night, "PORTION", 2)
    real, calls = judge_apply.apply, 0

    async def dying(*args: Any, **kwargs: Any) -> judge_apply.Applied:
        nonlocal calls
        calls += 1
        result = await real(*args, **kwargs)
        if calls == 2:
            raise _Killed
        return result

    monkeypatch.setattr(judge_apply, "apply", dying)
    with pytest.raises(_Killed):
        await _tick(session, FakePass())

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.APPLYING.value
    assert sum(confirmed is False for confirmed, _ in (await _decisions(session)).values()) == 2

    monkeypatch.setattr(judge_apply, "apply", real)
    await _tick(session, FakePass(), MORNING + timedelta(minutes=1))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    decided = await _decisions(session)
    assert {confirmed for confirmed, _ in decided.values()} == {False}
    assert len({by for _, by in decided.values()}) == 1, "партия одна и та же"
    assert [(entry.details or {})["применено"] for entry in await _portions(session)] == [2, 2, 1]
    assert night.applied is not None
    assert night.applied["skip"]["applied"] == 5


async def test_the_night_cap_holds_and_the_rest_goes_the_next_night(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = [f"site-{number}.example.test" for number in range(5)]
    await disputed(session, *((target, "skip") for target in targets))
    await _on(session, Band.SKIP)
    monkeypatch.setattr(judge_night, "NIGHT_CAP", 3)

    await _tick(session, FakePass())

    night = await _night(session)
    assert night is not None
    assert night.applied is not None
    assert (night.applied["skip"]["applied"], night.applied["skip"]["over_cap"]) == (3, 2)
    assert sum(confirmed is False for confirmed, _ in (await _decisions(session)).values()) == 3

    await _tick(session, FakePass(), MORNING + timedelta(days=1))

    assert {confirmed for confirmed, _ in (await _decisions(session)).values()} == {False}


async def test_skipping_that_would_take_an_advertiser_off_is_left_to_a_human(
    session: AsyncSession,
) -> None:
    """Рекламодатель остался от ссылки, которую обход больше не находит: «не пишем» по спорной его
    сняло бы. Ночью такая пара не решается и названа числом; кнопка её применяет."""
    await disputed(session, ("orphan.example.test", "skip"), ("plain.example.test", "skip"))
    await promoted(session, "orphan.example.test")
    await _on(session, Band.SKIP)

    await _tick(session, FakePass())

    decided = await _decisions(session)
    assert decided["orphan.example.test"] == (None, None)
    assert decided["plain.example.test"][0] is False
    night = await _night(session)
    assert night is not None
    assert night.applied is not None
    assert (night.applied["skip"]["applied"], night.applied["skip"]["spared"]) == (1, 1)
    assert night.applied["skip"]["removed"] == 0

    by_hand = await judge_apply.apply(session, Band.SKIP, batch="skip-20261105-120000-a1b2")
    assert (by_hand.applied, by_hand.removed) == (1, 1)


async def test_advice_is_relabelled_in_full_before_the_bands(session: AsyncSession) -> None:
    """Перед применением — полный пересчёт советов нынешней таблицей: и тех строк, чьих пар в
    очереди нет."""
    await disputed(session, ("waiting.example.test", None))
    run = await crawled(session, "second-donor.example.test")
    await scored(session, run, "decided.example.test", verdict=Verdict.PENDING, confirmed=True)
    session.add_all(
        [
            judged("waiting.example.test", "skip", rules_version="advertiser-rules-v0"),
            judged(
                "decided.example.test", "skip", donor=run.host, rules_version="advertiser-rules-v0"
            ),
        ]
    )
    await _on(session, Band.SKIP)

    await _tick(session, FakePass())

    versions = await session.scalars(
        select(AdvertiserJudgementModel.rules_version).execution_options(populate_existing=True)
    )
    assert set(versions.all()) == {RULES_VERSION}
    assert (await _decisions(session))["waiting.example.test"][0] is False


# --- сбои и тишина ---------------------------------------------------------------------------------


async def test_a_fallen_step_is_said_once_and_not_repeated(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    await disputed(session, ("skip.example.test", "skip"))
    await _on(session, Band.SKIP)

    async def broken(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("пересчёт сломан")

    monkeypatch.setattr(judge_night, "relabel", broken)
    await _tick(session, FakePass())
    await _tick(session, FakePass(), MORNING + timedelta(minutes=1))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.FAILED.value
    assert night.note is not None
    assert "пересчёт сломан" in night.note
    assert len(alerts) == 1
    assert "не дошёл до конца" in alerts[0]
    assert (await _decisions(session))["skip.example.test"] == (None, None)


async def test_a_day_without_a_step_is_said_once_the_next_morning(
    session: AsyncSession, alerts: list[str]
) -> None:
    """Полосы включены давно, а вчера шага не было вовсе — круг обходов так и не кончился или
    процесс лежал: наутро это записано строкой вчерашней ночи и сказано один раз."""
    session.add(JudgeBandModel(band="skip", enabled=True, set_at=MORNING - timedelta(days=3)))
    session.add(CrawlNightModel(night=DAY, status=NightStatus.RUNNING.value))
    queue = FakePass()

    await _tick(session, queue)
    await _tick(session, queue, MORNING + timedelta(minutes=1))

    missed = await _night(session, DAY - timedelta(days=1))
    assert missed is not None
    assert missed.status == Phase.FAILED.value
    assert missed.note is not None
    assert "не начался" in missed.note
    assert len(alerts) == 1
    assert await _night(session) is None, "сегодняшний круг ещё идёт — шаг ждёт"


async def test_a_band_turned_on_today_owes_nothing_for_yesterday(
    session: AsyncSession, alerts: list[str]
) -> None:
    session.add(JudgeBandModel(band="skip", enabled=True, set_at=MORNING - timedelta(hours=1)))

    await _tick(session, FakePass())

    assert await _night(session, DAY - timedelta(days=1)) is None
    assert alerts == []


async def test_a_band_kept_off_yesterday_owes_nothing_for_it(
    session: AsyncSession, alerts: list[str]
) -> None:
    """Полосу включали давно, потом выключили и включили снова сегодня: вчера она стояла
    выключенной — шагу вчера и не полагалось идти, тревоги нет."""
    for enabled, ago in (
        (True, timedelta(days=3)),
        (False, timedelta(days=2)),
        (True, timedelta(hours=1)),
    ):
        session.add(JudgeBandModel(band="skip", enabled=enabled, set_at=MORNING - ago))
        await session.flush()

    await _tick(session, FakePass())

    assert await _night(session, DAY - timedelta(days=1)) is None
    assert alerts == []


async def test_the_tick_is_quiet_when_there_is_nothing_to_do(
    session: AsyncSession, caplog: pytest.LogCaptureFixture, alerts: list[str]
) -> None:
    """Проход идёт раз в минуту: без полос, после конца шага и с пустой очередью — ни строки в
    журнал процесса и ни одной тревоги."""
    queue = FakePass()
    with caplog.at_level("INFO", logger=judge_night.__name__):
        await _tick(session, queue)
        await _on(session, Band.SKIP, Band.WRITE)
        await _tick(session, queue, MORNING + timedelta(minutes=1))
        caplog.clear()
        await _tick(session, queue, MORNING + timedelta(minutes=2))
        await _tick(session, queue, MORNING + timedelta(minutes=3))

    assert caplog.records == []
    assert alerts == []
    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    assert queue.started == []


# --- ревизия базы ----------------------------------------------------------------------------------


def _tables(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)"),
        {"names": list(TABLES)},
    )
    return set(rows.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str], list[str]]:
    """Ревизия вниз и вверх на соединении теста: схема тестов собирается по моделям, и без этого
    файл ревизии впервые исполнился бы на выкатке."""
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _tables(connection)
        migration.upgrade()
        up = _tables(connection)
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return down, up, list(values.scalars())


async def test_the_revision_goes_down_and_up_and_the_step_runs_on_its_tables(
    session: AsyncSession,
) -> None:
    """Откат снимает обе таблицы, подъём возвращает их такими, что шаг на них идёт: полоса
    включается строкой истории, а дата шага держится уникальной строкой (`on conflict`)."""
    await disputed(session, ("skip.example.test", "skip"))
    connection = await session.connection()

    down, up, values = await connection.run_sync(_down_and_up)

    assert down == set()
    assert up == set(TABLES)
    assert values.count("advertiser_judge_bands_changed") == 1

    await _on(session, Band.SKIP)
    await _tick(session, FakePass())
    await _tick(session, FakePass(), MORNING + timedelta(minutes=1))

    night = await _night(session)
    assert night is not None
    assert night.status == Phase.DONE.value
    assert (await _decisions(session))["skip.example.test"][0] is False
