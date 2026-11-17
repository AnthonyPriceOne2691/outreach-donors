"""Автосборка и план работ: письма гипотез одного плана — пилот за пилотом (решение владельца 16.11).

Гипотеза плана — не кандидат автосборки, пока у гипотезы того же плана левее (`position` меньше)
есть лиды, которым сборка напишет первое письмо, — тем же решением сборки, что «Напишет»
(`queue_plan.writable_counts`). Этап не в счёт: остановленный пилот с такими лидами держит
следующий, а лиды, которым сборка не напишет, не держат никого. Письма к пересборке — не лиды без
письма. Гипотеза вне плана и другой план — как прежде.

Пилоты заводятся от последнего к первому: номер гипотезы растёт справа налево, и прежний выбор
(ни разу не собранная с меньшим номером — первой) отдал бы недостачу последнему пилоту, а правило,
сравнивающее номера гипотез вместо `position`, — не тому. Модель не зовётся: письма собирает
подделка (`w.CorridorRewriter`), тексты и адреса выдуманы.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from backend.features.sales import autobuild, hypotheses, queue
from backend.features.sales.plan_models import (
    PlanMode,
    PlanStage,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.features.sales.queue_start import build_job_id
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autobuild import _AutoJobs, _no_chain, _stoplisted

NOW = w.NOW


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def jobs() -> _AutoJobs:
    return _AutoJobs()


async def _pilots(session: AsyncSession, *names: str) -> list[int]:
    """План работ: листы `names` по порядку, у каждого — своя гипотеза, этап «шлёт письма».
    Гипотезы заводятся от последнего листа к первому; ответ — их номера по порядку листов."""
    made = {name: (await hypotheses.add(session, name, None)).id for name in reversed(names)}
    plan = SalesPlanModel(
        name=f"План «{names[0]}»",
        mode=PlanMode.ECONOMY.value,
        cap_usd=Decimal("50.00"),
        estimate={},
        digest="0" * 16,
    )
    session.add(plan)
    await session.flush()
    for position, name in enumerate(names, 1):
        session.add(
            SalesPlanHypothesisModel(
                plan_id=plan.id,
                hypothesis_id=made[name],
                position=position,
                sheet=name,
                markets=["de"],
                language="en",
                target=100,
                max_pages=1,
                cap_usd=Decimal("10.00"),
                stage=PlanStage.SENDING.value,
            )
        )
    await session.flush()
    return [made[name] for name in names]


async def _stop(session: AsyncSession, hypothesis_id: int) -> None:
    """Пилот остановлен: кончился бюджет — новых прогонов нет, собранные лиды остались."""
    budget = {"code": "budget", "words": "кончился бюджет", "at": NOW.isoformat()}
    await session.execute(
        update(SalesPlanHypothesisModel)
        .where(SalesPlanHypothesisModel.hypothesis_id == hypothesis_id)
        .values(stage=PlanStage.STOPPED.value, problems=[budget])
    )


async def _write(session: AsyncSession, hypothesis_id: int) -> None:
    """Сборка написала первые письма всем лидам гипотезы: без письма у неё не осталось никого."""
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=hypothesis_id, limit=10)


async def _candidates(session: AsyncSession) -> list[int]:
    """Кандидаты автосборки, как их видит проход: с письмами к пересборке из очереди."""
    stale = (await autobuild.queued(session)).stale
    return [
        hypothesis_id for hypothesis_id, _ in await autobuild.candidates(session, stale, now=NOW)
    ]


async def _renamed(session: AsyncSession) -> None:
    """Подпись «Отправителя» поправили: письма очереди собраны с прежней — к пересборке."""
    await w.settings(session, signature="Mira Testova\nA renamed made-up agency")


# --- два пилота ---------------------------------------------------------------------------------


async def test_the_next_pilot_waits_while_the_one_before_has_leads_to_write(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """У обоих пилотов есть кому писать, оба ни разу не собраны, второй — с меньшим номером:
    прежде недостача ушла бы второму, и письма пилотов пошли бы вперемешку. Теперь — первому."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await w.lead(session, first, "anna@pilot-one.example.test")
    await w.lead(session, second, "boris@pilot-two.example.test")

    found = await autobuild.run(session, jobs, now=NOW)

    assert second < first
    assert await _candidates(session) == [first]
    assert (found.hypothesis_id, found.name) == (first, "Пилот 1")
    [(args, _)] = jobs.enqueued
    assert args == (QUEUE_JOB, first, 20)


async def test_when_the_leads_before_run_out_the_next_pilot_is_built(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Первые письма всем лидам первого пилота собраны — черёд второго, а письма первого стоят в
    очереди раньше: номер письма — порядок отправки."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await w.lead(session, first, "anna@pilot-one.example.test")
    await w.lead(session, second, "boris@pilot-two.example.test")
    await _write(session, first)

    found = await autobuild.run(session, jobs, now=NOW)

    assert await _candidates(session) == [second]
    assert (found.have, found.hypothesis_id) == (1, second)
    [(args, _)] = jobs.enqueued
    assert args[1] == second


# --- три пилота ---------------------------------------------------------------------------------


async def test_three_pilots_each_waits_for_every_one_before_it(
    session: AsyncSession, world: w.World
) -> None:
    """Третий ждёт не только соседа: у второго лидов нет, у первого есть — третий ждёт первого.
    Первый исписан — черёд третьего; у второго появился лид — третий снова ждёт, теперь второго."""
    first, second, third = await _pilots(session, "Пилот 1", "Пилот 2", "Пилот 3")
    await w.lead(session, first, "anna@pilot-one.example.test")
    await w.lead(session, third, "vera@pilot-three.example.test")

    behind_first = await _candidates(session)
    await _write(session, first)
    third_free = await _candidates(session)
    await w.lead(session, second, "boris@pilot-two.example.test")
    behind_second = await _candidates(session)

    assert (behind_first, third_free, behind_second) == ([first], [third], [second])


# --- вне плана и другой план --------------------------------------------------------------------


async def test_a_hypothesis_outside_any_plan_takes_turns_as_before(
    session: AsyncSession, world: w.World, jobs: _AutoJobs
) -> None:
    """Гипотеза не из плана — кандидат рядом с первым пилотом, и недостача ходит между ними по
    давности сборки, как прежде; второй пилот ждёт своего черёда и в очередь не встаёт."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await w.lead(session, first, "anna@pilot-one.example.test")
    await w.lead(session, second, "boris@pilot-two.example.test")
    order = []
    for _ in range(3):
        found = await autobuild.run(session, jobs, now=NOW)
        assert found.hypothesis_id is not None
        order.append(found.hypothesis_id)
        jobs.known[build_job_id(found.hypothesis_id)].finish({"prepared": 0, "stopped": None})

    assert await _candidates(session) == [world.hypothesis_id, first]
    assert order == [world.hypothesis_id, first, world.hypothesis_id]


async def test_two_plans_do_not_hold_each_other(session: AsyncSession, world: w.World) -> None:
    """Черёд — внутри плана: второй пилот плана «Б» не ждёт первого пилота плана «А», хотя тот
    левее по номеру листа; у первого пилота «Б» лидов нет — второй «Б» не ждёт никого."""
    a_first, a_second = await _pilots(session, "А: пилот 1", "А: пилот 2")
    b_first, b_second = await _pilots(session, "Б: пилот 1", "Б: пилот 2")
    await w.lead(session, a_first, "anna@plan-a-one.example.test")
    await w.lead(session, a_second, "boris@plan-a-two.example.test")
    await w.lead(session, b_second, "vera@plan-b-two.example.test")

    found = await _candidates(session)

    assert sorted(found) == sorted([a_first, b_second])
    assert b_first not in found


# --- остановленный пилот ------------------------------------------------------------------------


async def test_a_stopped_pilot_with_leads_to_write_still_goes_first(
    session: AsyncSession, world: w.World
) -> None:
    """Пилот остановлен (кончился бюджет): новых прогонов нет, а собранные лиды у него остались, и
    сборка им пишет — их письма первыми, следующий ждёт."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await _stop(session, first)
    await w.lead(session, first, "anna@pilot-one.example.test")
    await w.lead(session, second, "boris@pilot-two.example.test")

    assert await _candidates(session) == [first]


@pytest.mark.parametrize("unwritten", [_no_chain, _stoplisted])
async def test_leads_the_build_will_not_write_hold_no_one(
    session: AsyncSession, world: w.World, unwritten: Any
) -> None:
    """У остановленного пилота лиды «готов» без письма, но сборка им не напишет: цепочки их языка
    нет (проблема плана `chain`) или адрес в стоп-листе. Решение — одно со сборкой и «Напишет»:
    следующий не ждёт, иначе план встал бы на лидах, которым письма не будет."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await _stop(session, first)
    await unwritten(session, first)
    await w.lead(session, second, "boris@pilot-two.example.test")

    assert await _candidates(session) == [second]


# --- письма к пересборке ------------------------------------------------------------------------


async def test_letters_to_rebuild_before_do_not_hold_the_next_pilot(
    session: AsyncSession, world: w.World
) -> None:
    """Первый пилот исписан, а его письмо собрано с прежней подписью: в очереди оно на своём месте
    (пересборка — на том же), лидов без письма у первого нет — второй не ждёт."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await w.lead(session, first, "anna@pilot-one.example.test")
    await _write(session, first)
    await w.lead(session, second, "boris@pilot-two.example.test")
    await _renamed(session)

    assert (await autobuild.queued(session)).stale == {first}
    assert await _candidates(session) == [second, first]


async def test_a_waiting_pilot_with_letters_to_rebuild_is_no_candidate(
    session: AsyncSession, world: w.World
) -> None:
    """Второй пилот собран кнопкой раньше, чем у первого появились лиды, и его письмо — к
    пересборке. Пока первому есть кому писать, второй не кандидат: его сборка писала бы и новым
    лидам. Первый исписан — второй снова кандидат, письмо пересоберётся."""
    first, second = await _pilots(session, "Пилот 1", "Пилот 2")
    await w.lead(session, second, "boris@pilot-two.example.test")
    await _write(session, second)
    await w.lead(session, first, "anna@pilot-one.example.test")
    await _renamed(session)

    waiting = await _candidates(session)
    await _write(session, first)

    assert (await autobuild.queued(session)).stale == {second}
    assert (waiting, await _candidates(session)) == ([first], [second])
