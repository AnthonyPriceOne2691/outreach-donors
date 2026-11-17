"""Обход 24/7 на экране: состояние, пуск и пауза, порог, переобход слабых, пометка донора руками.

Проверяется то, ради чего маршруты так устроены: ответ — ровно по договору с экраном; смотреть
может любой с правом этапа, менять — только с правом запуска, и каждая смена остаётся в журнале
«было → стало»; рубильник сервиса сильнее выключателя; порог держится границ и шага; строка блока
говорит, почему включённый обход стоит; доноры вкладки несут свою глубину. И ревизия схемы — вниз и
вверх, и опечатка в настройке не роняет процессы.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.crawls import always as api
from backend.api.crawls import routes
from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as cfg
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.crawl import (
    CrawlAlwaysSignalModel,
    CrawlBacklogModel,
    CrawlDepthModel,
    CrawlRunModel,
)
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import always_switch, always_view
from backend.features.crawl.units_stop import UnitsStop
from backend.shared.crawl_marks import Held
from backend.shared.numbers import spaced
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.migration_helpers import load_migration

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/crawls/always"
RUNNER = "запуск@site.test"
VIEWER = "смотрит@site.test"
MIGRATION = "ed07677f52ff_crawl_always.py"
TABLES = ("crawl_always_switches", "crawl_depths", "crawl_always_signals")
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)

#: Поля ответа — ровно по договору с экраном (бриф 17.11.2026).
VIEW_KEYS = {
    "available",
    "enabled",
    "set_at",
    "set_by",
    "threshold_pct",
    "recrawl_weak",
    "waiting_for",
    "counts",
    "pages",
    "part_pages",
    "words",
    "details",
    "yields",
    "done_at",
}
COUNT_KEYS = {"new", "first", "deeper", "weak", "exhausted", "running", "total"}
DEPTH_KEYS = {
    "pages",
    "parts",
    "selling_pages",
    "yield_pct",
    "weak",
    "weak_by",
    "exhausted",
    "running",
}


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Рубильник включён, ночного круга нет, очередь обходов и сторож нагрузки не спрашиваются."""
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)
    monkeypatch.setattr(routes, "workers_alive", lambda **_: 4)
    monkeypatch.setattr(routes, "crawlers_held", lambda **_: Held(0))
    monkeypatch.setattr(routes, "job_positions", lambda *_args, **_kwargs: {})


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Очередь обходов в памяти: «Обойти всех по фильтру» ставит в неё сразу."""
    put: list[int] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        put.append(run_id)
        return job_id or "x"

    monkeypatch.setattr(routes, "enqueue_crawl", enqueue)
    return put


@pytest.fixture
async def runner(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(RUNNER, role=UserRole.OPERATOR)
    return bearer(await sign_in(RUNNER))


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(VIEWER, permissions={"run": False})
    return bearer(await sign_in(VIEWER))


async def _donor(session: AsyncSession, host: str) -> None:
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price, donor.last_price_at = Decimal("120.00"), datetime.now(UTC) - timedelta(days=2)
    await session.commit()


async def _depth(session: AsyncSession, host: str, pages: int, **more: object) -> None:
    session.add(CrawlRunModel(host=host, status=CrawlStatus.DONE, outcome=CrawlOutcome.OK))
    session.add(CrawlDepthModel(host=host, pages=pages, **more))
    await session.commit()


async def _journal(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.CRAWL_ALWAYS_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return list(rows.all())


# --- состояние и выключатель -----------------------------------------------------------------------


async def test_the_block_is_paused_until_someone_starts_it(
    client: AsyncClient, viewer: dict[str, str], session: AsyncSession
) -> None:
    await _donor(session, "new.test")

    answer = await client.get(URL, headers=viewer)

    assert answer.status_code == 200
    body = answer.json()
    assert set(body) == VIEW_KEYS
    assert set(body["counts"]) == COUNT_KEYS
    assert (body["available"], body["enabled"], body["recrawl_weak"]) == (True, False, False)
    assert (body["threshold_pct"], body["set_by"], body["set_at"]) == (0.1, None, None)
    assert (body["waiting_for"], body["done_at"], body["yields"], body["pages"]) == (
        None,
        None,
        [],
        0,
    )
    assert body["counts"] == dict.fromkeys(COUNT_KEYS, 0) | {"new": 1, "total": 1}
    assert body["part_pages"] == 10_000
    assert body["words"] == "на паузе; осталось: новые — 1"
    assert any("0,1 %" in line for line in body["details"])


async def test_start_threshold_recrawl_and_pause_each_leave_a_journal_entry(
    client: AsyncClient, runner: dict[str, str], session: AsyncSession
) -> None:
    started = await client.post(URL, json={"enabled": True}, headers=runner)
    again = await client.post(URL, json={"enabled": True}, headers=runner)
    raised = await client.post(URL, json={"threshold_pct": 0.25}, headers=runner)
    paused = await client.post(URL, json={"enabled": False, "recrawl_weak": True}, headers=runner)

    assert [one.status_code for one in (started, again, raised, paused)] == [200] * 4
    body = started.json()
    assert (body["enabled"], body["set_by"]) == (True, RUNNER)
    assert body["set_at"] is not None
    assert raised.json()["threshold_pct"] == 0.25
    assert (paused.json()["enabled"], paused.json()["recrawl_weak"]) == (False, True)
    assert paused.json()["threshold_pct"] == 0.25, "не названное остаётся как стоит"
    entries = await _journal(session)
    assert [(entry.target, entry.details) for entry in entries] == [
        ("crawls:always", {"обход 24/7": {"было": "на паузе", "стало": "идёт"}}),
        ("crawls:always", {"порог «мало рекламодателей», %": {"было": 0.1, "стало": 0.25}}),
        (
            "crawls:always",
            {
                "обход 24/7": {"было": "идёт", "стало": "на паузе"},
                "переобход слабых": {"было": "выключен", "стало": "включён"},
            },
        ),
    ], "та же настройка второй раз записи не заводит"


@pytest.mark.parametrize(
    "body",
    [{"threshold_pct": 100.5}, {"threshold_pct": -0.01}, {"threshold_pct": 0.123}, {"pause": True}],
    ids=["больше ста", "меньше нуля", "мельче сотой", "лишнее поле"],
)
async def test_the_threshold_keeps_its_range_and_step(
    client: AsyncClient, runner: dict[str, str], body: dict[str, object]
) -> None:
    answer = await client.post(URL, json=body, headers=runner)

    assert answer.status_code == 422


async def test_seeing_needs_the_stage_and_changing_needs_the_run_right(
    client: AsyncClient, viewer: dict[str, str], session: AsyncSession
) -> None:
    await _donor(session, "donor.test")

    assert (await client.get(URL)).status_code == 403
    assert (await client.get(URL, headers=viewer)).status_code == 200
    assert (await client.post(URL, json={"enabled": True}, headers=viewer)).status_code == 403
    marked = await client.post(
        f"{URL}/weak", json={"host": "donor.test", "weak": True}, headers=viewer
    )
    assert marked.status_code == 403
    assert await _journal(session) == []


async def test_the_service_switch_beats_the_button(
    client: AsyncClient,
    runner: dict[str, str],
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _donor(session, "donor.test")
    monkeypatch.setattr(cfg, "ENABLED", False)

    seen = await client.get(URL, headers=runner)
    refused = await client.post(URL, json={"enabled": True}, headers=runner)
    marked = await client.post(
        f"{URL}/weak", json={"host": "donor.test", "weak": True}, headers=runner
    )

    assert (seen.json()["available"], seen.json()["words"]) == (False, always_view.UNAVAILABLE)
    assert refused.status_code == 409
    assert refused.json()["detail"] == api.SWITCHED_OFF
    assert marked.status_code == 200, "пометка — оценка сайта, а не пуск"
    assert (await always_switch.state(session)).enabled is False


# --- пометка донора руками -------------------------------------------------------------------------


async def test_a_donor_is_marked_by_hand_unmarked_and_handed_back_to_the_threshold(
    client: AsyncClient, runner: dict[str, str], session: AsyncSession
) -> None:
    await _donor(session, "www.donor.test")
    await _depth(session, "donor.test", 10_000, first_done_at=NOW, yield_pct=0.02)

    marked = await client.post(
        f"{URL}/weak", json={"host": "WWW.Donor.test", "weak": True}, headers=runner
    )
    unmarked = await client.post(
        f"{URL}/weak", json={"host": "donor.test", "weak": False}, headers=runner
    )
    back = await client.post(
        f"{URL}/weak", json={"host": "donor.test", "weak": None}, headers=runner
    )
    unknown = await client.post(
        f"{URL}/weak", json={"host": "nobody.test", "weak": True}, headers=runner
    )

    assert set(marked.json()) == DEPTH_KEYS
    assert marked.json() == {
        "pages": 10_000,
        "parts": 0,
        "selling_pages": None,
        "yield_pct": 0.02,
        "weak": True,
        "weak_by": "human",
        "exhausted": False,
        "running": False,
    }
    assert (unmarked.json()["weak"], unmarked.json()["weak_by"]) == (False, "human")
    assert (back.json()["weak"], back.json()["weak_by"]) == (True, "auto"), "0,02 % < 0,1 %"
    assert unknown.status_code == 404
    entries = await _journal(session)
    assert [(entry.target, entry.details) for entry in entries] == [
        (
            "domain:donor.test",
            {"мало рекламодателей": {"было": "решает порог", "стало": "помечен руками"}},
        ),
        (
            "domain:donor.test",
            {"мало рекламодателей": {"было": "помечен руками", "стало": "пометка снята руками"}},
        ),
        (
            "domain:donor.test",
            {"мало рекламодателей": {"было": "пометка снята руками", "стало": "решает порог"}},
        ),
    ]


async def test_donor_rows_carry_their_depth(
    client: AsyncClient, runner: dict[str, str], session: AsyncSession
) -> None:
    await _donor(session, "deep.test")
    await _donor(session, "plain.test")
    await _depth(
        session, "deep.test", 20_000, parts=3, first_done_at=NOW, yield_pct=1.5, selling_pages=300
    )

    answer = await client.get("/api/crawls/donors", headers=runner)

    rows = {row["host"]: row for row in answer.json()["rows"]}
    assert rows["deep.test"]["depth"] == {
        "pages": 20_000,
        "parts": 3,
        "selling_pages": 300,
        "yield_pct": 1.5,
        "weak": False,
        "weak_by": None,
        "exhausted": False,
        "running": False,
    }
    assert rows["plain.test"]["depth"] is None


async def test_the_tab_filters_and_counts_weak_donors_under_its_other_conditions(
    client: AsyncClient,
    runner: dict[str, str],
    session: AsyncSession,
    queue: list[int],
) -> None:
    for host in ("weak-auto.test", "weak-hand.test", "strong.test", "plain.test"):
        await _donor(session, host)
    await _depth(session, "weak-auto.test", 10_000, first_done_at=NOW, yield_pct=0.02)
    await _depth(session, "weak-hand.test", 3_000, weak_override=True)
    await _depth(session, "strong.test", 10_000, first_done_at=NOW, yield_pct=2.0)

    weak = await client.get("/api/crawls/donors", params={"weak": "true"}, headers=runner)
    strong = await client.get("/api/crawls/donors", params={"weak": "false"}, headers=runner)
    searched = await client.get(
        "/api/crawls/donors", params={"search": "hand", "status": "done"}, headers=runner
    )
    started = await client.post(
        "/api/crawls/donors/crawl", json={"weak": True, "expected": 10}, headers=runner
    )

    assert sorted(row["host"] for row in weak.json()["rows"]) == [
        "weak-auto.test",
        "weak-hand.test",
    ]
    assert weak.json()["total"] == 2
    assert weak.json()["counts"]["weak"] == 2
    assert weak.json()["counts"]["status"]["done"] == 2, "статус — под условием пометки"
    assert sorted(row["host"] for row in strong.json()["rows"]) == ["plain.test", "strong.test"]
    assert strong.json()["counts"]["weak"] == 2, "счётчик пометки — без своего условия"
    assert searched.json()["counts"]["weak"] == 1, "под остальными условиями вкладки"
    assert started.json()["asked"] == 2, "«Обойти всех по фильтру» — те же условия"


# --- строка блока ----------------------------------------------------------------------------------


async def _view(session: AsyncSession, now: datetime = NOW) -> always_view.View:
    return await always_view.view(session, now=now)


async def test_the_line_says_what_goes_and_what_is_left(session: AsyncSession) -> None:
    await _donor(session, "new.test")
    await _donor(session, "first.test")
    await _depth(session, "first.test", 3_000)
    await always_switch.set_switch(session, by=None, enabled=True)

    found = await _view(session)

    assert found.waiting_for is None
    assert found.words == f"идёт: новые — 1 · до {spaced(10_000)} — 1; обходится сейчас 0"
    assert (found.pages, found.part_pages) == (3_000, 10_000)
    assert not any(spaced(3_000) in line for line in found.details), "страницы экран пишет сам"


async def test_the_line_says_why_a_running_round_waits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _donor(session, "new.test")
    await always_switch.set_switch(session, by=None, enabled=True)
    session.add(CrawlBacklogModel(host="human.test", requested_by="человек@site.test"))
    await session.flush()
    assert (await _view(session)).waiting_for == always_view.HUMANS_WAIT

    async def stopped(_session: AsyncSession) -> UnitsStop:
        return UnitsStop("закончились юниты Ahrefs", "подробно", "ждать")

    monkeypatch.setattr(always_view, "units_stop", stopped)
    assert (await _view(session)).waiting_for == always_view.UNITS_WAIT
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", True)
    at_night = await _view(session, datetime(2026, 11, 17, 0, 0, tzinfo=UTC))

    assert at_night.waiting_for == always_view.NIGHT_WAIT
    assert at_night.words == f"ждёт: {always_view.NIGHT_WAIT}; осталось: новые — 1"


async def test_all_done_is_said_and_dated(session: AsyncSession) -> None:
    await _donor(session, "done.test")
    await _donor(session, "weak.test")
    await _depth(session, "done.test", 4_000, first_done_at=NOW, exhausted_at=NOW)
    await _depth(session, "weak.test", 10_000, first_done_at=NOW, yield_pct=0.0)
    await always_switch.set_switch(session, by=None, enabled=True)
    session.add(CrawlAlwaysSignalModel(epoch=1, sent_at=NOW))
    await session.flush()

    found = await _view(session)

    assert found.waiting_for == always_view.DONE_WAIT
    assert found.words == "пройдено всё: до конца — 1 · мало рекламодателей — 1"
    assert found.done_at == NOW
    assert found.counts["exhausted"] == found.counts["weak"] == 1


async def test_no_donors_is_said_in_words(session: AsyncSession) -> None:
    await always_switch.set_switch(session, by=None, enabled=True)

    assert (await _view(session)).waiting_for == always_view.NO_DONORS


async def test_yields_are_of_the_sites_whose_mark_the_threshold_decides(
    session: AsyncSession,
) -> None:
    for host, pages, more in (
        ("a.test", 10_000, {"first_done_at": NOW, "yield_pct": 4.0}),
        ("b.test", 10_000, {"first_done_at": NOW, "yield_pct": 0.05}),
        ("c.test", 3_000, {"first_done_at": NOW, "exhausted_at": NOW, "yield_pct": 0.5}),
        ("d.test", 10_000, {"first_done_at": NOW, "yield_pct": 0.0, "weak_override": False}),
        ("e.test", 2_000, {}),
    ):
        await _donor(session, host)
        await _depth(session, host, pages, **more)

    found = await _view(session)

    assert found.yields == [0.05, 0.5, 4.0], "пометка руками от порога не зависит"
    assert found.counts["weak"] == 1
    assert found.counts["exhausted"] == 1


# --- ревизия и настройка ---------------------------------------------------------------------------


def _tables(connection: Connection) -> tuple[set[str], bool]:
    tables = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)"),
        {"names": list(TABLES)},
    )
    column = connection.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'crawl_runs' AND column_name = 'depth_part'"
        )
    )
    return set(tables.scalars()), column.first() is not None


def _down_and_up(connection: Connection) -> tuple[tuple[set[str], bool], tuple[set[str], bool]]:
    """Ревизия вниз и вверх на соединении теста: иначе файл ревизии впервые исполнился бы на
    выкатке."""
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _tables(connection)
        migration.upgrade()
    return down, _tables(connection)


async def test_the_revision_goes_down_and_up_and_the_round_works_on_it(
    session: AsyncSession,
) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(_down_and_up)
    values = await connection.execute(
        text(
            "SELECT unnest(enum_range(NULL::auditaction))::text "
            "UNION ALL SELECT unnest(enum_range(NULL::stopreason))::text"
        )
    )

    assert down == (set(), False)
    assert up == (set(TABLES), True)
    assert sorted(
        value for value in values.scalars() if value in {"crawl_always_changed", "paused"}
    ) == [
        "crawl_always_changed",
        "paused",
    ]
    await always_switch.set_switch(session, by=None, enabled=True, threshold_pct=0.5)
    assert (await always_switch.state(session)).threshold_pct == 0.5


#: Разбор со сторожем грузит настройку обхода 24/7: опечатка в ней не должна ронять процесс.
PROBE = (
    "import backend.workers.reaper\n"
    "from backend.features.crawl import backlog\n"
    "from backend.config import crawl_always\n"
    "print(crawl_always.ENABLED, crawl_always.SLOTS, sorted(crawl_always._unread))"
)


@pytest.mark.parametrize(
    ("values", "said"),
    [
        ({}, "True 12 []"),
        ({"CRAWL_ALWAYS": "false", "CRAWL_ALWAYS_SLOTS": "6"}, "False 6 []"),
        ({"CRAWL_ALWAYS": "да"}, "False 12 ['CRAWL_ALWAYS']"),
        ({"CRAWL_ALWAYS_SLOTS": "много"}, "True 12 ['CRAWL_ALWAYS_SLOTS']"),
    ],
    ids=["умолчание", "задано", "опечатка в рубильнике", "опечатка в числе частей"],
)
def test_the_service_switch_is_on_by_default_and_a_typo_turns_it_off(
    values: dict[str, str], said: str
) -> None:
    env = {key: one for key, one in os.environ.items() if not key.startswith("CRAWL_ALWAYS")}
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env={**env, **values, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == said
