"""Автосборка очереди продаж: не хватает писем на сутки — та же задача сборки, что у кнопки.

Очередь задач — подставная с правилами rq 2.12 (`test_sales_clean_api._Jobs`): номер задачи,
`unique`, итог отдельно от задачи; плюс мета задачи — ею помечена сборка, поставленная сама.
Сборку проход только ставит: модель в этих тестах не зовётся — письмо к пересборке собирает
подделка модели (`w.CorridorRewriter`). Тексты и адреса выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.sales import autobuild, corridor, hypotheses, queue, queue_plan, queue_start
from backend.features.sales.autobuild import Plan
from backend.features.sales.models import SalesHandoffModel, SalesStoplistModel
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.features.sales.queue_start import LIMIT_MAX, build_job_id
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_clean_api import _Job, _Jobs

NOW = w.NOW


class _AutoJobs(_Jobs):
    """Подставная очередь, которая помнит мету задачи, как rq."""

    def enqueue(self, *args: object, **kwargs: object) -> _Job:
        job = super().enqueue(*args, **kwargs)
        job.meta = dict(kwargs.get("meta") or {})  # type: ignore[attr-defined]
        return job


class _Silent(_Jobs):
    """Очередь задач не отвечает."""

    def fetch_job(self, job_id: str) -> _Job | None:
        raise RedisConnectionError("очередь не отвечает")


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def jobs() -> _AutoJobs:
    return _AutoJobs()


async def _ready(session: AsyncSession, hypothesis_id: int, *emails: str) -> None:
    for email in emails:
        await w.lead(session, hypothesis_id, email)


async def _second(session: AsyncSession, name: str = "Вторая выдуманная гипотеза") -> int:
    found = await hypotheses.add(session, name, None)
    return found.id


def _auto_builds(session: AsyncSession) -> Any:
    return session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.RUN_STARTED)
        .order_by(AuditLogModel.id)
    )


# --- не хватает — одна сборка с пределом -------------------------------------------------------


async def test_short_of_a_day_one_build_with_the_shortage_as_its_limit(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Ящик продаж с капом 20 — сутки; в очереди ничего — сборка гипотезы на 20 писем."""
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")

    found = await autobuild.run(session, jobs, now=NOW)

    assert (found.need, found.have, found.short, found.limit) == (20, 0, 20, 20)
    [(args, kwargs)] = jobs.enqueued
    assert args == (QUEUE_JOB, world.hypothesis_id, 20)
    assert kwargs["job_id"] == build_job_id(world.hypothesis_id)
    assert kwargs["meta"] == {queue_start.AUTO: True}
    [record] = list(await _auto_builds(session))
    assert (record.user_id, record.target) == (None, f"job:{build_job_id(world.hypothesis_id)}")
    assert record.details == {
        "этап": "sales",
        "действие": autobuild.AUTO,
        "гипотеза": "Выдуманная гипотеза",
        "писем": 20,
        "не хватало": 20,
    }


async def test_the_limit_is_the_shortage_but_never_above_the_build_limit(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    world.sales_box.daily_cap = LIMIT_MAX + 37
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")

    found = await autobuild.run(session, jobs, now=NOW)

    assert (found.short, found.limit) == (LIMIT_MAX + 37, LIMIT_MAX)
    [(args, _)] = jobs.enqueued
    assert args[2] == LIMIT_MAX


async def test_enough_letters_for_a_day_no_build(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    world.sales_box.daily_cap = 0
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")

    found = await autobuild.run(session, jobs, now=NOW)

    # Окно мира открыто во все дни: ближайший день отправки — сегодня.
    assert found == Plan(need=0, have=0, day=NOW.date())
    assert jobs.enqueued == []


# --- две сборки разом — нельзя -----------------------------------------------------------------


async def test_a_running_build_of_any_hypothesis_holds_the_next_one(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Кнопкой собирают вторую гипотезу — автосборка первую не ставит: недостача считается по
    письмам в очереди, и вторая сборка разом собрала бы её ещё раз."""
    other = await _second(session)
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    queue_start.start(jobs, other, 5)

    found = await autobuild.run(session, jobs, now=NOW)

    assert len(jobs.enqueued) == 1
    assert (
        found.why
        == "идёт сборка гипотезы «Вторая выдуманная гипотеза» — недостача посчитается после неё"
    )


async def test_after_the_running_build_ends_the_next_one_is_set(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    await autobuild.run(session, jobs, now=NOW)
    held = await autobuild.run(session, jobs, now=NOW)
    jobs.known[build_job_id(world.hypothesis_id)].finish({"prepared": 0, "stopped": None})
    again = await autobuild.run(session, jobs, now=NOW)

    assert held.why is not None
    assert held.why.startswith("идёт сборка гипотезы «Выдуманная гипотеза»")
    assert again.hypothesis_id == world.hypothesis_id
    assert len(jobs.enqueued) == 2


# --- тумблеры, потолок модели, серия вне коридора ----------------------------------------------


@pytest.mark.parametrize(("enabled", "autobuild_on"), [(True, False), (False, True)])
async def test_switched_off_no_build(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, autobuild_on: bool
) -> None:
    called: list[datetime] = []

    async def forbidden(*_: object, now: datetime) -> Plan:
        called.append(now)
        raise AssertionError("автосборка прошла при выключенном тумблере")

    monkeypatch.setattr(autobuild, "run", forbidden)
    monkeypatch.setattr(sales_cfg, "ENABLED", enabled)
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", autobuild_on)

    await autobuild.tick()

    assert called == []


async def test_model_cap_reached_no_build_and_the_words_of_the_cap(
    session: AsyncSession, world: w.World, jobs: _AutoJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 41)
    usage.record(session, operation="sales_letter_rewrite", units=43)
    await session.flush()

    # Сегодня по часам базы: расход записан ею, а потолок считается с начала суток прохода.
    found = await autobuild.run(session, jobs, now=datetime.now(UTC))

    assert jobs.enqueued == []
    assert found.why is not None
    assert "потолок расхода на модель за день достигнут" in found.why


async def test_a_hypothesis_whose_last_build_stopped_off_the_corridor_waits_for_a_person(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    other = await _second(session)
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    await _ready(session, other, "olga@beta.example.test")
    job = queue_start.start(jobs, world.hypothesis_id, 5)
    jobs.known[job.id].finish({"prepared": 0, "stopped": corridor.streak_stop()})

    skipped = await autobuild.run(session, jobs, now=NOW)
    jobs.known[build_job_id(other)].finish({"prepared": 0, "stopped": corridor.streak_stop()})
    both = await autobuild.run(session, jobs, now=NOW)
    # Человек поправил цепочку и собрал кнопкой — итог прежней сборки снят, автосборка снова её.
    queue_start.start(jobs, world.hypothesis_id, 5)
    jobs.known[build_job_id(world.hypothesis_id)].finish({"prepared": 0, "stopped": None})
    resumed = await autobuild.run(session, jobs, now=NOW)

    assert skipped.hypothesis_id == other
    assert (both.hypothesis_id, both.why) == (None, autobuild.STREAK)
    assert resumed.hypothesis_id == world.hypothesis_id


# --- какие гипотезы и по очереди ---------------------------------------------------------------


async def test_only_hypotheses_with_a_chain_and_ready_leads_without_a_letter(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    empty = await _second(session, "Без лидов")
    no_chain = await _second(session, "Без цепочки")
    await _ready(session, no_chain, "olga@beta.example.test")
    await w.lead(session, no_chain, "ivan@gamma.example.test", language="de")

    found = await autobuild.candidates(session, now=NOW)

    # Общая цепочка мира — на ru и en: en-лиду «без цепочки» сборка напишет, de-лиду — нет.
    assert [hypothesis_id for hypothesis_id, _ in found] == [no_chain]
    assert empty not in [hypothesis_id for hypothesis_id, _ in found]
    planned = await autobuild.plan(session, jobs, now=NOW)
    assert planned.hypothesis_id == no_chain


async def test_without_a_candidate_it_says_so(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    found = await autobuild.run(session, jobs, now=NOW)

    assert (found.hypothesis_id, found.why) == (None, autobuild.NO_CANDIDATES)
    assert jobs.enqueued == []


async def _no_chain(session: AsyncSession, hypothesis_id: int) -> None:
    """Лид на языке, цепочки на котором нет (у мира — en и ru)."""
    await w.lead(session, hypothesis_id, "jonas@fahrrad.example.test", language="de")


async def _stoplisted(session: AsyncSession, hypothesis_id: int) -> None:
    """Лид «готов», а его адрес — в стоп-листе продаж."""
    await w.lead(session, hypothesis_id, "stop@two.example.test")
    session.add(SalesStoplistModel(email="stop@two.example.test"))
    await session.flush()


async def _handed(session: AsyncSession, hypothesis_id: int) -> None:
    """Лид «готов», а его передали телемаркетологу."""
    lead = await w.lead(session, hypothesis_id, "handed@one.example.test")
    campaign = CampaignModel(stage=Stage.SALES, name="Выдуманная рассылка", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=lead.domain_id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    session.add(SalesHandoffModel(thread_id=thread.id, lead_id=lead.id))
    await session.flush()


@pytest.mark.parametrize("unwritten", [_no_chain, _stoplisted, _handed])
async def test_a_hypothesis_whose_ready_leads_the_build_will_not_write_is_no_candidate(
    session: AsyncSession, world: w.World, jobs: _AutoJobs, unwritten: Any
) -> None:
    """Лид «готов» без письма, но сборка ему не напишет — решение одно со сборкой и с «Напишет»
    «Рассылки» (`queue_plan`): гипотеза не кандидат, проход не ставит пустую сборку каждые десять
    минут. Лид, которому сборка напишет, — и гипотеза снова кандидат."""
    await unwritten(session, world.hypothesis_id)

    idle = await autobuild.run(session, jobs, now=NOW)
    counted = await queue_plan.writable_counts(session, now=NOW)
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    found = await autobuild.run(session, jobs, now=NOW)

    assert counted == {}
    assert (idle.hypothesis_id, idle.why) == (None, autobuild.NO_CANDIDATES)
    assert await queue_plan.writable_counts(session, now=NOW) == {world.hypothesis_id: 1}
    assert found.hypothesis_id == world.hypothesis_id
    [(args, _)] = jobs.enqueued
    assert args == (QUEUE_JOB, world.hypothesis_id, 20)
    [record] = list(await _auto_builds(session))
    assert record.target == f"job:{build_job_id(world.hypothesis_id)}"


async def test_letters_to_rebuild_keep_the_hypothesis_a_candidate(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Письмо в очереди собрано с прежней подписью — отправка его не пустит, а сборка соберёт
    заново: гипотеза — кандидат, хотя лидов без письма у неё нет и «Напишет» — ноль."""
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    built = await autobuild.plan(session, jobs, now=NOW)
    await w.settings(session, signature="Mira Testova\nA renamed made-up agency")

    found = await autobuild.run(session, jobs, now=NOW)

    assert (built.have, built.hypothesis_id, built.why) == (1, None, autobuild.NO_CANDIDATES)
    assert await queue_plan.writable_counts(session, now=NOW) == {}
    assert (found.have, found.hypothesis_id) == (0, world.hypothesis_id)
    [(args, _)] = jobs.enqueued
    assert args == (QUEUE_JOB, world.hypothesis_id, 20)


async def test_the_shortage_goes_to_hypotheses_in_turn(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Ни разу не собранная — первой; затем та, чья сборка начиналась давнее всех."""
    other = await _second(session)
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")
    await _ready(session, other, "olga@beta.example.test")
    order = []
    for _ in range(4):
        found = await autobuild.run(session, jobs, now=NOW)
        order.append(found.hypothesis_id)
        assert found.hypothesis_id is not None
        jobs.known[build_job_id(found.hypothesis_id)].finish({"prepared": 0, "stopped": None})

    assert order == [world.hypothesis_id, other, world.hypothesis_id, other]


async def test_a_silent_job_queue_is_said_in_words(session: AsyncSession, world: w.World) -> None:
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")

    found = await autobuild.run(session, _Silent(), now=NOW)

    assert (found.hypothesis_id, found.why) == (None, autobuild.QUEUE_SILENT)


async def test_two_passes_while_the_build_waits_in_the_queue_set_it_once(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    await _ready(session, world.hypothesis_id, "jane@acme.example.test")

    for _ in range(3):
        await autobuild.run(session, jobs, now=NOW)

    assert len(jobs.enqueued) == 1
    assert jobs.known[build_job_id(world.hypothesis_id)].get_status() is JobStatus.QUEUED
