"""Автокруг рекламодателей на экране и в очереди задач: выключатель, итог круга и те же задачи и
номера, что у кнопок.

Проверяется то, ради чего маршруты и очередь так устроены: включает автокруг только учётка с правом
запуска и правом отправки разом, и это остаётся в журнале; рубильник сервиса сильнее выключателя;
итог круга отдаётся словами сервера; круг ставит сборку под номером кнопки «Собрать очередь» и не
ставит вторую рядом со сборкой человека, а поиск адресов — той же задачей, что «Перевести».
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from backend.api.advertisers import auto_round as api
from backend.api.advertisers import routes
from backend.api.letters import once
from backend.config import auto_round as cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import AutoRoundModel
from backend.features.crawl import auto_round_queue, auto_round_view, judge_night_queue
from backend.features.crawl.auto_round_queue import (
    BUILD_JOB_ID,
    SEARCH_MAX,
    WORKER_DIED,
    RqRound,
)
from backend.features.crawl.auto_round_report import (
    EMPTY,
    MISSED_WHY,
    OFF_MIDWAY,
    SENDING,
    Phase,
    RoundReport,
)
from backend.features.crawl.auto_round_switch import DEFAULT_LETTERS, MOST
from backend.features.ops.job_outcome import JobOutcome
from backend.shared.queue import (
    ADVERTISER_CONTACTS_JOB_KEY,
    BUILD_JOB,
    CONTACTS_JOB,
    RESULT_TTL,
)
from httpx import AsyncClient
from redis.exceptions import RedisError
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letters_one_job import _Jobs

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/advertisers/auto-round"
ROOT = Path(__file__).resolve().parents[1]
SENDER = "отправитель@example.com"


@pytest.fixture
async def sender(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор с правом отправки: перевод, поиск, сборка и отправка — все три кнопки."""
    await make_user(SENDER, role=UserRole.OPERATOR, permissions={"send": True})
    return bearer(await sign_in(SENDER))


async def _changes(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.ADVERTISER_AUTO_ROUND_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return list(rows.all())


# --- выключатель -----------------------------------------------------------------------------------


async def test_the_round_is_off_and_nothing_has_run_yet(
    client: AsyncClient, sender: dict[str, str]
) -> None:
    answer = await client.get(URL, headers=sender)

    assert answer.status_code == 200
    body = answer.json()
    assert body["available"] is True
    assert body["switch"] == {
        "enabled": False,
        "letters": DEFAULT_LETTERS,
        "set_by": None,
        "set_at": None,
    }
    assert (body["last"], body["ran_today"]) == (None, False)
    assert (body["letters_min"], body["letters_max"]) == (1, MOST)
    assert body["first"].startswith(("Первый круг", "Сегодняшнего круга"))


async def test_turning_on_changing_the_cap_and_off_each_leave_a_journal_entry(
    client: AsyncClient, sender: dict[str, str], session: AsyncSession
) -> None:
    on = await client.post(URL, json={"enabled": True, "letters": 100}, headers=sender)
    again = await client.post(URL, json={"enabled": True, "letters": 100}, headers=sender)
    capped = await client.post(URL, json={"enabled": True, "letters": 40}, headers=sender)
    off = await client.post(URL, json={"enabled": False, "letters": 40}, headers=sender)

    assert [one.status_code for one in (on, again, capped, off)] == [200, 200, 200, 200]
    switch = on.json()["switch"]
    assert (switch["enabled"], switch["letters"], switch["set_by"]) == (True, 100, SENDER)
    assert switch["set_at"] is not None
    assert off.json()["switch"]["enabled"] is False
    entries = await _changes(session)
    assert [(entry.target, entry.details) for entry in entries] == [
        ("advertisers:auto-round", {"автокруг": {"было": "выключен", "стало": "включён"}}),
        ("advertisers:auto-round", {"писем в сутки": {"было": 100, "стало": 40}}),
        ("advertisers:auto-round", {"автокруг": {"было": "включён", "стало": "выключен"}}),
    ], "та же настройка второй раз записи не заводит"


@pytest.mark.parametrize("missing", [Permission.RUN, Permission.SEND])
async def test_switching_needs_both_rights_and_seeing_needs_neither(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    missing: Permission,
) -> None:
    email = f"без-{missing.value}@example.com"
    await make_user(email, role=UserRole.OPERATOR, permissions={"send": True, missing.value: False})
    headers = bearer(await sign_in(email))

    seen = await client.get(URL, headers=headers)
    refused = await client.post(URL, json={"enabled": True, "letters": 50}, headers=headers)

    assert seen.status_code == 200
    assert refused.status_code == 403
    assert refused.json()["detail"] == no_right(missing)
    assert await _changes(session) == []


@pytest.mark.parametrize(
    ("method", "body"), [("GET", None), ("POST", {"enabled": True, "letters": 50})]
)
async def test_without_a_pass_nobody_sees_or_switches(
    client: AsyncClient, session: AsyncSession, method: str, body: dict[str, Any] | None
) -> None:
    """Общая таблица маршрутов рекламодателей (`tests/test_api_advertisers.py`) этот маршрут не
    ведёт: у оператора по умолчанию нет права отправки. Без пропуска — отказ, как у всех."""
    answer = await client.request(method, URL, json=body)

    assert answer.status_code == 403
    assert await _changes(session) == []


@pytest.mark.parametrize("letters", [0, MOST + 1])
async def test_a_cap_outside_the_bounds_is_refused_in_words(
    client: AsyncClient, sender: dict[str, str], letters: int
) -> None:
    answer = await client.post(URL, json={"enabled": True, "letters": letters}, headers=sender)

    assert answer.status_code == 422
    assert f"от 1 до {MOST}" in answer.text


async def test_the_service_setting_off_hides_the_switch_and_refuses_it(
    client: AsyncClient,
    sender: dict[str, str],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "ENABLED", False)

    seen = await client.get(URL, headers=sender)
    refused = await client.post(URL, json={"enabled": True, "letters": 50}, headers=sender)

    assert seen.json()["available"] is False
    assert (refused.status_code, refused.json()["detail"]) == (409, api.SWITCHED_OFF)
    assert await _changes(session) == []


async def test_the_last_round_is_said_in_the_servers_words(
    client: AsyncClient, sender: dict[str, str], session: AsyncSession
) -> None:
    report = RoundReport(
        promoted=24,
        searched=True,
        found=20,
        paid=9,
        campaign="Автокруг 05.11 — рекламодатели по ссылке",
        letters=100,
        prepared=20,
        waiting=40,
        queue=SENDING,
        dropped=2,
    )
    finished = datetime(2026, 11, 5, 5, 12, tzinfo=UTC)
    session.add(
        AutoRoundModel(
            day=date(2026, 11, 5),
            status=Phase.DONE.value,
            started_at=finished,
            finished_at=finished,
            report=asdict(report),
        )
    )
    await session.commit()

    last = (await client.get(URL, headers=sender)).json()["last"]

    assert last["words"] == (
        "переведено 24 · адресов найдено 20 (платных 9) · собрано 20 из 40 · очередь уходит сама"
    )
    assert (last["day"], last["going"]) == ("2026-11-05", False)
    assert last["at"].startswith("2026-11-05T05:12")
    assert "перевод: отсеяно стоп-листами — 2" in last["lines"]


# --- очередь задач глазами круга -------------------------------------------------------------------


def test_the_round_uses_the_buttons_numbers_and_bounds() -> None:
    assert once.build_job_id(Stage.ADVERTISERS, "links") == BUILD_JOB_ID
    assert routes.SEARCH_MAX == SEARCH_MAX


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Подставная очередь задач с правилами rq: задача держит номер, пока её не убрали."""
    found = _Jobs()
    monkeypatch.setattr(auto_round_queue, "runs_queue", lambda: found)
    monkeypatch.setattr(auto_round_queue, "job_alive", lambda _id: True)
    # Итог прежней задачи постановка стирает в Redis мимо неё — здесь стирать нечего.
    monkeypatch.setattr(auto_round_queue.Result, "delete_all", lambda _job: None)
    return found


def test_the_build_is_asked_under_the_buttons_number_with_the_cap(jobs: _Jobs) -> None:
    assert RqRound().build("Автокруг 05.11 — рекламодатели по ссылке", 30) == "asked"

    ((args, options),) = jobs.enqueued
    assert args == (BUILD_JOB, "Автокруг 05.11 — рекламодатели по ссылке", "us")
    assert (options["job_id"], options["unique"]) == (BUILD_JOB_ID, True)
    assert (options["limit"], options["stage"]) == (30, Stage.ADVERTISERS.value)
    assert "followup_days" not in options, "сроки добивок — умолчание настроек"
    assert options["result_ttl"] == RESULT_TTL


def test_a_build_started_by_a_human_is_waited_for_and_not_doubled(jobs: _Jobs) -> None:
    jobs.enqueue(BUILD_JOB, "Май", job_id=BUILD_JOB_ID, unique=True)

    assert RqRound().build("Автокруг", 30) == "busy"
    assert len(jobs.enqueued) == 1


def test_a_finished_build_and_a_dead_workers_build_give_the_number_back(
    jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    RqRound().build("Автокруг", 30)
    jobs.known[BUILD_JOB_ID].status = JobStatus.FINISHED

    assert RqRound().build("Автокруг", 20) == "asked"

    jobs.known[BUILD_JOB_ID].status = JobStatus.STARTED
    monkeypatch.setattr(auto_round_queue, "job_alive", lambda _id: False)

    assert RqRound().build("Автокруг", 10) == "asked"
    assert [options["limit"] for _, options in jobs.enqueued] == [30, 20, 10]


def test_a_silent_queue_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def down() -> None:
        raise RedisError("очередь не отвечает")

    monkeypatch.setattr(auto_round_queue, "runs_queue", down)
    monkeypatch.setattr(auto_round_queue, "contacts_job_id", lambda **_k: None)
    monkeypatch.setattr(auto_round_queue, "contacts_queue", down)

    assert RqRound().build("Автокруг", 30) == "silent"
    assert RqRound().search(5) is None


class _Contacts:
    def __init__(self) -> None:
        self.enqueued: list[tuple[Any, ...]] = []
        self.remembered: list[tuple[str, str]] = []

    def enqueue(self, *args: Any, **_kwargs: Any) -> object:
        self.enqueued.append(args)
        return type("Job", (), {"id": "job-адреса"})()

    def remember(self, job_id: str, *, key: str) -> None:
        self.remembered.append((job_id, key))


def test_the_search_is_the_buttons_job_and_a_running_one_is_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacts = _Contacts()
    monkeypatch.setattr(auto_round_queue, "contacts_queue", lambda: contacts)
    monkeypatch.setattr(auto_round_queue, "remember_contacts_job", contacts.remember)
    monkeypatch.setattr(auto_round_queue, "contacts_job_id", lambda **_k: None)

    assert RqRound().search(5_000) is True

    assert contacts.enqueued == [
        (CONTACTS_JOB, SEARCH_MAX, False, False, None, Stage.ADVERTISERS.value)
    ]
    assert contacts.remembered == [("job-адреса", ADVERTISER_CONTACTS_JOB_KEY)]

    monkeypatch.setattr(auto_round_queue, "contacts_job_id", lambda **_k: "job-идёт")
    monkeypatch.setattr(auto_round_queue, "job_alive", lambda _id: True)

    assert RqRound().search(3) is True
    assert len(contacts.enqueued) == 1, "второй поиск заплатил бы платной ступени дважды"


@pytest.mark.parametrize(
    ("outcome", "alive", "state"),
    [
        (None, None, "idle"),
        (JobOutcome(BUILD_JOB_ID, "сборка", "queued"), None, "going"),
        (JobOutcome(BUILD_JOB_ID, "сборка", "running"), False, "failed"),
        (JobOutcome(BUILD_JOB_ID, "сборка", "done", report={"prepared": 3}), None, "done"),
        (
            JobOutcome(BUILD_JOB_ID, "сборка", "refused", error="шаблон не разбирается"),
            None,
            "failed",
        ),
    ],
)
def test_the_build_is_read_into_what_the_round_needs(
    monkeypatch: pytest.MonkeyPatch, outcome: JobOutcome | None, alive: bool | None, state: str
) -> None:
    # Задачу круг читает тем же чтением, что ночной шаг судьи (`judge_night_queue.seen_of`).
    monkeypatch.setattr(judge_night_queue, "job_outcome", lambda _id: outcome)
    monkeypatch.setattr(judge_night_queue, "job_alive", lambda _id: alive)

    seen = RqRound().build_seen()

    assert seen.state == state
    if alive is False:
        assert seen.error == WORKER_DIED


# --- слова итога и первого круга -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "report", "note", "words"),
    [
        ("searching", RoundReport(promoted=3), None, "ждёт поиск адресов"),
        ("failed", RoundReport(), "сборка отказала", "встал — сборка отказала"),
        ("missed", RoundReport(), MISSED_WHY, f"не начался — {MISSED_WHY}"),
        ("stopped", RoundReport(promoted=2), OFF_MIDWAY, "выключен посреди круга · переведено 2"),
        (
            "done",
            RoundReport(promoted=1, campaign="Автокруг", prepared=4, stopped="потолок"),
            None,
            "переведено 1 · собрано 4 — упёрлось в потолок модели",
        ),
        (
            "done",
            RoundReport(promoted=1, stopped="потолок", queue=EMPTY),
            None,
            f"переведено 1 · сборки не было — потолок модели выбран · {EMPTY}",
        ),
    ],
)
def test_the_round_is_said_in_words_whatever_happened(
    status: str, report: RoundReport, note: str | None, words: str
) -> None:
    moment = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)
    row = AutoRoundModel(
        day=date(2026, 11, 5),
        status=status,
        report=asdict(report),
        note=note,
        started_at=None if status == "missed" else moment,
        finished_at=moment,
    )

    summary = auto_round_view.summary_of(row)

    assert summary.words == words
    assert summary.going is (status == "searching")
    assert (summary.at is None) is (status == "missed"), "у пропущенной даты времени нет"


@pytest.mark.parametrize(
    ("hour", "ran_today", "first"),
    [
        (0, False, "Первый круг — сегодня, после ночного шага судьи, не позже 12:00 по Москве."),
        (10, False, "Сегодняшнего круга ещё не было: он начнётся в ближайшие минуты."),
        (10, True, "Первый круг — завтра утром, после ночного шага судьи."),
        (18, False, "Первый круг — завтра утром, после ночного шага судьи."),
    ],
)
async def test_the_first_round_is_named_before_turning_on(
    session: AsyncSession, hour: int, ran_today: bool, first: str
) -> None:
    """Время — по UTC: 10 — это 13:00 по Москве, 18 — 21:00, позже часа догона."""
    if ran_today:
        session.add(AutoRoundModel(day=date(2026, 11, 5), status=Phase.DONE.value))
        await session.flush()

    found = await auto_round_view.view(session, now=datetime(2026, 11, 5, hour, 0, tzinfo=UTC))

    assert (found.first, found.ran_today) == (first, ran_today)


# --- рубильник сервиса -----------------------------------------------------------------------------

#: Разбор со сторожем грузит настройку автокруга: опечатка в ней не должна ронять процесс.
PROBE = (
    "import backend.workers.reaper\n"
    "from backend.config import auto_round\n"
    "print(auto_round.ENABLED, sorted(auto_round._unread))"
)


@pytest.mark.parametrize(
    ("value", "said"),
    [(None, "True []"), ("false", "False []"), ("да", "False ['ADVERTISERS_AUTO_ROUND']")],
)
def test_the_service_setting_is_on_by_default_and_a_typo_turns_it_off(
    value: str | None, said: str
) -> None:
    env = {key: one for key, one in os.environ.items() if key != "ADVERTISERS_AUTO_ROUND"}
    if value is not None:
        env["ADVERTISERS_AUTO_ROUND"] = value
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == said
