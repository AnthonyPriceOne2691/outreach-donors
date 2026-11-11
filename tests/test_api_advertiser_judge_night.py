"""«Судья сам, каждую ночь» на экране и в очереди: переключатель полос, итог ночи и разбор спорных
той же задачей, что у кнопки.

Проверяется то, ради чего маршруты и очередь так устроены: полосу включает только учётка с правом
запуска и правом решения разом, и это остаётся в журнале; итог ночи отдаётся числами и строками как
есть; ночной шаг не ставит второй разбор рядом с тем, что запустил человек, и не принимает задачу
умершего обходчика за идущую.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.api.advertisers import judge_night as api
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.crawl import judge_night_queue
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_night import NIGHT_CAP
from backend.features.crawl.judge_night_queue import WORKER_DIED, RqPass
from backend.features.crawl.night import NIGHT_BY
from backend.features.ops.job_outcome import JobOutcome
from backend.shared.queue import (
    JUDGE_DISPUTED_JOB,
    JUDGE_DISPUTED_JOB_ID,
    JUDGE_DISPUTED_TIMEOUT,
    RESULT_TTL,
)
from httpx import AsyncClient
from redis.exceptions import RedisError
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_advertiser_judge_apply import disputed
from tests.test_advertiser_judge_night import DAY, MORNING, FakePass, _on, _tick
from tests.test_letters_one_job import _Jobs

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/advertisers/judge-night"
OPERATOR = "оператор@site.example.test"


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(OPERATOR, role=UserRole.OPERATOR)
    return bearer(await sign_in(OPERATOR))


async def _changes(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.ADVERTISER_JUDGE_BANDS_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return list(rows.all())


# --- переключатель полос ---------------------------------------------------------------------------


async def test_bands_are_off_and_nothing_has_run_yet(
    client: AsyncClient, operator: dict[str, str]
) -> None:
    answer = await client.get(URL, headers=operator)

    assert answer.status_code == 200
    assert answer.json() == {
        "bands": [
            {"band": "skip", "enabled": False, "set_by": None, "set_at": None},
            {"band": "write", "enabled": False, "set_by": None, "set_at": None},
        ],
        "last": None,
        "ran_today": False,
        "awaiting_promotion": 0,
        "cap": NIGHT_CAP,
    }


async def test_a_band_is_turned_on_and_off_with_a_journal_entry_each_time(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    on = await client.post(f"{URL}/bands", json={"band": "skip", "enabled": True}, headers=operator)
    again = await client.post(
        f"{URL}/bands", json={"band": "skip", "enabled": True}, headers=operator
    )

    assert (on.status_code, again.status_code) == (200, 200)
    skip, write = on.json()["bands"]
    assert (skip["band"], skip["enabled"], skip["set_by"]) == ("skip", True, OPERATOR)
    assert skip["set_at"] is not None
    assert write["enabled"] is False, "полосы включаются по одной"

    off = await client.post(
        f"{URL}/bands", json={"band": "skip", "enabled": False}, headers=operator
    )

    assert off.json()["bands"][0]["enabled"] is False
    entries = await _changes(session)
    assert [(entry.target, entry.details) for entry in entries] == [
        (
            "advertisers:judge-night",
            {"полоса": "skip", "автоприменение": {"было": "выключено", "стало": "включено"}},
        ),
        (
            "advertisers:judge-night",
            {"полоса": "skip", "автоприменение": {"было": "включено", "стало": "выключено"}},
        ),
    ], "та же настройка второй раз записи не заводит"


@pytest.mark.parametrize("missing", [Permission.RUN, Permission.PRICES])
async def test_turning_a_band_on_needs_both_rights_and_seeing_it_does_not(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    missing: Permission,
) -> None:
    await make_user("без-права@site.example.test", permissions={missing.value: False})
    headers = bearer(await sign_in("без-права@site.example.test"))

    seen = await client.get(URL, headers=headers)
    refused = await client.post(
        f"{URL}/bands", json={"band": "write", "enabled": True}, headers=headers
    )

    assert seen.status_code == 200
    assert (refused.status_code, refused.json()["detail"]) == (403, no_right(missing))
    assert await _changes(session) == []


async def test_a_band_that_does_not_exist_and_an_extra_field_are_refused(
    client: AsyncClient, operator: dict[str, str]
) -> None:
    unclear = await client.post(
        f"{URL}/bands", json={"band": "unclear", "enabled": True}, headers=operator
    )
    extra = await client.post(
        f"{URL}/bands", json={"band": "skip", "enabled": True, "tonight": True}, headers=operator
    )

    assert (unclear.status_code, extra.status_code) == (422, 422)


# --- итог ночи -------------------------------------------------------------------------------------


async def test_the_night_is_shown_in_numbers_and_in_lines_as_they_are(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    await disputed(
        session,
        ("skip.example.test", "skip"),
        ("write.example.test", "write"),
        ("new.example.test", None),
    )
    await _on(session, Band.SKIP, Band.WRITE)
    queue = FakePass()
    await _tick(session, queue)

    going = (await client.get(URL, headers=operator)).json()["last"]

    assert (going["status"], going["going"]) == ("judging", "идёт разбор спорных")

    queue.finish(
        MORNING + timedelta(minutes=20),
        advice={"unclear": 1},
        lines=["рассужены: 1", "советы: не понять 1", "токенов: 4 000"],
        stopped="дневной потолок судьи выбран",
    )
    await _tick(session, queue, MORNING + timedelta(minutes=21))

    body = (await client.get(URL, headers=operator)).json()

    last = body["last"]
    assert (last["night"], last["status"], last["going"]) == (DAY.isoformat(), "done", None)
    assert (last["judged"], last["applied"], last["unclear"]) == (1, {"skip": 1, "write": 1}, 1)
    assert last["why"] == "разбор встал: дневной потолок судьи выбран"
    assert last["lines"] == [
        "рассужены: 1",
        "советы: не понять 1",
        "токенов: 4 000",
        "разбор встал: дневной потолок судьи выбран",
        "«не пишем»: применено 1",
        "«пишем»: применено 1",
    ]
    # «Пишем» ночью никого не переводит: домен ждёт «Перевести».
    assert body["awaiting_promotion"] == 1
    assert body["ran_today"] is False, "ночь из прошлого — сегодняшний шаг ещё не шёл"


async def test_what_the_night_left_to_a_human_is_named_in_the_lines(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession, monkeypatch: Any
) -> None:
    from backend.features.crawl import judge_night  # noqa: PLC0415 — потолок ночи одному тесту

    monkeypatch.setattr(judge_night, "NIGHT_CAP", 1)
    await disputed(session, ("one.example.test", "skip"), ("two.example.test", "skip"))
    await _on(session, Band.SKIP)
    await _tick(session, FakePass())

    last = (await client.get(URL, headers=operator)).json()["last"]

    assert last["lines"][-1] == ("«не пишем»: применено 1; сверх потолка ночи, пойдут следующей: 1")


# --- разбор спорных в очереди обходов --------------------------------------------------------------


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Подставная очередь обходов с правилами rq: задача держит номер, пока её не убрали."""
    found = _Jobs()
    monkeypatch.setattr(judge_night_queue, "crawl_queue", lambda: found)
    monkeypatch.setattr(judge_night_queue, "job_alive", lambda _id: True)
    # Итог прежней задачи постановка стирает в Redis мимо неё — здесь стирать нечего.
    monkeypatch.setattr(judge_night_queue.Result, "delete_all", lambda _job: None)
    return found


def test_the_night_asks_for_the_same_job_under_the_same_number_as_the_button(jobs: _Jobs) -> None:
    assert RqPass().start(7, by=NIGHT_BY) is True

    ((args, options),) = jobs.enqueued
    assert args == (JUDGE_DISPUTED_JOB, 7)
    assert options["job_id"] == JUDGE_DISPUTED_JOB_ID
    assert (options["unique"], options["at_front"]) == (True, True)
    assert (options["job_timeout"], options["result_ttl"]) == (JUDGE_DISPUTED_TIMEOUT, RESULT_TTL)
    assert options["meta"] == {"by": NIGHT_BY, "total": 7}


def test_a_pass_started_by_a_human_is_waited_for_and_not_doubled(jobs: _Jobs) -> None:
    RqPass().start(7, by="оператор")

    assert RqPass().start(3, by=NIGHT_BY) is False
    assert len(jobs.enqueued) == 1


def test_a_finished_job_and_a_dead_workers_job_give_the_number_back(
    jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    RqPass().start(7, by=NIGHT_BY)
    jobs.known[JUDGE_DISPUTED_JOB_ID].status = JobStatus.FINISHED

    assert RqPass().start(3, by=NIGHT_BY) is True

    # Обходчик умер вместе с задачей: статус «идёт» держится до её срока, а ключа воркера нет.
    jobs.known[JUDGE_DISPUTED_JOB_ID].status = JobStatus.STARTED
    monkeypatch.setattr(judge_night_queue, "job_alive", lambda _id: False)

    assert RqPass().start(2, by=NIGHT_BY) is True
    assert [args for args, _ in jobs.enqueued] == [
        (JUDGE_DISPUTED_JOB, 7),
        (JUDGE_DISPUTED_JOB, 3),
        (JUDGE_DISPUTED_JOB, 2),
    ]


def test_a_silent_queue_is_said_as_unknown_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> None:
        raise RedisError("очередь не отвечает")

    monkeypatch.setattr(judge_night_queue, "crawl_queue", down)

    assert RqPass().start(5, by=NIGHT_BY) is None


@pytest.mark.parametrize(
    ("outcome", "alive", "state"),
    [
        (None, None, "idle"),
        (
            JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "unknown", error="нет связи"),
            None,
            "unknown",
        ),
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "queued"), None, "going"),
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "running"), True, "going"),
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "running"), False, "failed"),
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "failed", error="упала"), None, "failed"),
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "done", report={"done": 3}), None, "done"),
        # Кончилась без отчёта — не итог: шаг поставит разбор снова.
        (JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "done"), None, "failed"),
    ],
)
def test_the_queue_is_read_into_what_the_step_needs(
    monkeypatch: pytest.MonkeyPatch, outcome: JobOutcome | None, alive: bool | None, state: str
) -> None:
    monkeypatch.setattr(judge_night_queue, "job_outcome", lambda _id: outcome)
    monkeypatch.setattr(judge_night_queue, "job_alive", lambda _id: alive)

    seen = RqPass().seen()

    assert seen.state == state
    if state == "failed" and alive is False:
        assert seen.error == WORKER_DIED


def test_the_end_of_a_job_is_read_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Старые версии очереди отдают время без пояса: шаг сравнивает его с минутой, когда спросил."""
    ended = datetime(2026, 11, 5, 6, 0)
    done = JobOutcome(JUDGE_DISPUTED_JOB_ID, "разбор", "done", report={"done": 1}, ended_at=ended)
    monkeypatch.setattr(judge_night_queue, "job_outcome", lambda _id: done)

    assert RqPass().seen().ended_at == ended.replace(tzinfo=UTC)
    assert api.SWITCH == {True: "включено", False: "выключено"}
