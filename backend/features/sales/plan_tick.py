"""Конвейер «Плана работ»: один проход — счёт, переходы гипотез плана и не больше одной постановки.

Проход по расписанию процесса разбора (`workers/reaper.py`, раз в `SALES_PLAN_PASS_SEC`) со своей
сессией, как у автосборки (`autobuild.tick`); выключен по умолчанию (`config/sales_plan.py`). Решения
владельца 16.11: экономный судья — Flex (режим плана → выдача и тариф, `plan_store.PLAN_MODES`),
конвейеру — доля дневного потолка модели продаж, письма — «пилот за пилотом» (автосборка).

**Один прогон плана за раз на весь сервис.** Идёт прогон плана (`segment_watch.unfinished`) — проход
пересчитывает числа его гипотезы и ждёт; очередь не ответила — тоже «идёт». Задачи прогона нет или
умер её воркер (`segment_watch.watch`) — проход продолжает его, как «Продолжить» экрана; у плана на
паузе брошенный прогон ждёт человека и конвейер не держит.

**Планы** — идущие, первым тот, чей прогон кончился давнее; **гипотезы плана** — по `position`.
Строки плана и гипотезы проход берёт `FOR UPDATE SKIP LOCKED`: кнопки экрана и проход не пишут их
одновременно, занятую проход пропускает до следующего раза.

**«Одна вперёд»** (п. 3.7): собирает одна гипотеза плана за раз — первая `collecting`; нет такой —
первая `waiting`, если до неё не больше одной гипотезы в `sending`. Следующая начинает, когда текущая
набрала цель и шлёт письма; k+2 ждёт, пока k не `done` (`stopped` — тоже конец: конвейер идёт дальше).

**Переходы** (п. 3.1): найдено ≥ цели → `sending`; `sending` → `done`, когда у лидов гипотезы не
осталось первого письма (`plan_settle.letters_left`); план — `done`, когда все гипотезы в `done` или
`stopped`. «Нужны ключи» и потолки — у постановки прогона (`plan_runs.py`).

**Гипотезы в конце** (`done`, `stopped`) — числа без переходов и `counted_at`: найдено,
израсходовано и резерв (`plan_settle.numbers`). Прогонов им проход не ставит, а числа меняют прогон,
который доходит после «Остановить» (окно «Качество сайтов» показывает «Израсходовано» из строки), и
решения человека (пересчёт «найдено» — сразу, `plan_found.recount`).
Найдено ≥ цели у остановленной — не переход: правило «цель набрана → письма» проход применяет к
гипотезе, чей ход, а остановленная хода не получает; «Остановить» человека проход не перебивает, и
в письмах она держала бы очередь «одна вперёд». Снова в работу её ставит «Продолжить» — тогда её ход
и переведёт её в письма. `counted_at` не трогается: по нему продолженная гипотеза отдаст итог своего
прогона в реестр ключей (`_fresh`).

**После прогона** — один раз: прогон кончился позже последнего взгляда прохода (`counted_at`) —
купленное и отработанные потерянные ключи (`plan_settle.settle`), числа, переходы. Прогон встал — вид
по словам (`plan_failures.of_stop`): у гипотезы — `stopped` и проблема `run_failed` словами
`run.error`; у сервиса — план на паузу теми же словами; временный — проблема и продолжение того же
прогона (`plan_runs.resume`: выдачу второй раз не покупает) не чаще `SALES_PLAN_RETRY_MIN`. Встал, а
гипотезу или план снова пустили — тот же прогон продолжается: «Продолжить» (срез 5) значит
«попробовать снова». Иначе — новый прогон по пачке волны (`plan_runs.new_run`).

**Переходы — в журнал процесса** строкой с полями (`plan_moves`); ожидание — строкой при смене
причины.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from rq import Queue
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import sales_plan as plan_cfg
from backend.config import storage
from backend.features.sales.plan_budget import Problem, Spent, spending
from backend.features.sales.plan_failures import Kind, of_stop
from backend.features.sales.plan_gates import Gates
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import (
    ACTED,
    NOTHING,
    Did,
    Turn,
    finish,
    move,
    pause,
    trouble,
)
from backend.features.sales.plan_runs import new_run, resume
from backend.features.sales.plan_settle import count, letters_left, numbers, settle
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_watch import GOING, watch
from backend.shared.queue import sales_bulk_queue

logger = logging.getLogger(__name__)

Run = SalesSegmentRunModel
Row = SalesPlanHypothesisModel
Plan = SalesPlanModel

TARGET_MET = "цель набрана — лиды уходят в письма"
TURN_CAME = "очередь гипотезы: собирает она"
LETTERS_GONE = "первые письма лидов гипотезы ушли"
PLAN_DONE = "все гипотезы плана в «готово» или «остановлена»"
SILENT_STOP = "прогон остановлен без слов причины — продолжите прогон"
#: Гипотеза в конце: дальше конвейер её не собирает.
FINAL = frozenset({PlanStage.DONE, PlanStage.STOPPED})
#: В `sending` у гипотез до той, что начинает собирать, — не больше: «одна вперёд».
AHEAD = 1


@dataclass
class Said:
    """Почему конвейер ждёт — сказанное журналом последним: строка только при смене."""

    why: str | None = None

    def tell(self, turn: Turn) -> None:
        why = turn.why if turn.did is Did.WAITED else None
        if why is not None and why != self.why:
            logger.info("конвейер плана ждёт", extra={"why": why})
        self.why = why


SAID = Said()


async def tick() -> None:
    """Проход по расписанию (`workers/reaper.py`): тумблеры, своя сессия, очередь долгих задач
    продаж (`sales-bulk`) — та же, что у «Запустить» сбора."""
    if not (sales_cfg.ENABLED and plan_cfg.ENABLED):
        return
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            SAID.tell(await step(session, sales_bulk_queue(), now=datetime.now(UTC)))
    finally:
        await engine.dispose()


async def step(session: AsyncSession, jobs: Queue, *, now: datetime) -> Turn:
    """Один проход: идущий прогон плана — числа и ждать; иначе планы по очереди — переходы и не
    больше одной постановки на весь проход."""
    gates = Gates(session, jobs, now)
    for run in await _unfinished(session):
        turn = await _going(session, gates, run, now)
        if turn is not None:
            return turn
    seen = NOTHING
    for plan_id in await _running(session):
        turn = await _plan(session, gates, plan_id, now)
        await session.commit()
        if turn.did in ACTED:
            return turn
        seen = turn if seen.did is Did.NOTHING else seen
    return seen


async def _unfinished(session: AsyncSession) -> list[SalesSegmentRunModel]:
    """Прогоны плана, которые идут или поставлены продолжением (`segment_watch.unfinished`)."""
    found = await session.scalars(
        select(Run)
        .where(
            Run.plan_hypothesis_id.is_not(None),
            or_(Run.stage.in_(sorted(GOING)), Run.finished_at.is_(None)),
        )
        .order_by(Run.id)
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _running(session: AsyncSession) -> list[int]:
    """Идущие планы: первым тот, чей прогон кончился давнее, без прогонов — раньше всех."""
    last = (
        select(func.max(Run.finished_at))
        .join(Row, Row.id == Run.plan_hypothesis_id)
        .where(Row.plan_id == Plan.id)
        .scalar_subquery()
    )
    found = await session.scalars(
        select(Plan.id)
        .where(Plan.status == PlanStatus.RUNNING)
        .order_by(last.asc().nulls_first(), Plan.id)
    )
    return list(found.all())


async def _locked[M: (SalesPlanModel, SalesPlanHypothesisModel)](
    session: AsyncSession, model: type[M], number: int | None
) -> M | None:
    """Строка под замком до конца транзакции; занята кнопкой — `None`, проход её пропускает."""
    if number is None:
        return None
    found: M | None = await session.scalar(
        select(model)
        .filter_by(id=number)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return found


async def _going(
    session: AsyncSession, gates: Gates, run: SalesSegmentRunModel, now: datetime
) -> Turn | None:
    """Идущий прогон плана держит конвейер: числа его гипотезы — и ждать. Задачи нет — продолжить,
    если план идёт; у плана на паузе брошенный прогон не держит (`None`)."""
    seen = await asyncio.to_thread(watch, run.id, run.stage, workers=None)
    row = await _locked(session, Row, run.plan_hypothesis_id)
    if row is None:
        return Turn(Did.GOING, run_id=run.id)
    if seen.going or seen.silent:
        await count(session, row, (await spending(session, row.plan_id)).get(row.id, Spent()), now)
        await session.commit()
        return Turn(Did.GOING, run_id=run.id)
    plan = await session.get(Plan, row.plan_id, populate_existing=True)
    turn = None
    if plan is not None and plan.status == PlanStatus.RUNNING:
        turn = await resume(session, gates, row, run, now)
    await session.commit()
    return turn


async def _plan(session: AsyncSession, gates: Gates, plan_id: int, now: datetime) -> Turn:
    """План: переходы гипотез (`_moves`), потом числа гипотез, что были в конце до прохода: их
    строки под замком только до коммита прохода, а не пока ход ставит прогон и спрашивает
    смету."""
    plan = await _locked(session, Plan, plan_id)
    if plan is None or plan.status != PlanStatus.RUNNING:
        return NOTHING
    found = await session.scalars(
        select(Row)
        .where(Row.plan_id == plan.id)
        .order_by(Row.position)
        .execution_options(populate_existing=True)
    )
    rows = list(found.all())
    ended = [row for row in rows if row.stage in FINAL]
    spent = await spending(session, plan.id)
    turn = await _moves(session, gates, plan, rows, spent, now)
    for row in ended:
        await _ended(session, row, spent.get(row.id, Spent()))
    return turn


async def _moves(
    session: AsyncSession,
    gates: Gates,
    plan: SalesPlanModel,
    rows: list[SalesPlanHypothesisModel],
    spent: dict[int, Spent],
    now: datetime,
) -> Turn:
    """Гипотезы в письмах — к концу, следующая по очереди — её ход; все в конце — план тоже."""
    for row in rows:
        if row.stage == PlanStage.SENDING:
            await _sending(session, row, spent, now)
    chosen = _next_row(rows)
    if chosen is not None:
        return await _turn(session, gates, plan, chosen, spent, now)
    if all(row.stage in FINAL for row in rows):
        return finish(plan, now, why=PLAN_DONE)
    return NOTHING


async def _sending(
    session: AsyncSession, row: SalesPlanHypothesisModel, spent: dict[int, Spent], now: datetime
) -> None:
    """Гипотеза шлёт письма: числа — и `done`, когда первых писем её лидов не осталось."""
    locked = await _locked(session, Row, row.id)
    if locked is None or locked.stage != PlanStage.SENDING:
        return
    await count(session, row, spent.get(row.id, Spent()), now)
    if not await letters_left(session, row.hypothesis_id, now=now):
        move(row, PlanStage.DONE, now, why=LETTERS_GONE)


async def _ended(session: AsyncSession, row: SalesPlanHypothesisModel, spent: Spent) -> None:
    """Гипотеза в конце: числа без переходов и `counted_at` (шапка модуля)."""
    locked = await _locked(session, Row, row.id)
    if locked is None or locked.stage not in FINAL:
        return
    await numbers(session, row, spent)


def _next_row(rows: list[SalesPlanHypothesisModel]) -> SalesPlanHypothesisModel | None:
    """Чей ход по правилу «одна вперёд» (шапка модуля); ничей — `None`."""
    collecting = next((row for row in rows if row.stage == PlanStage.COLLECTING), None)
    if collecting is not None:
        return collecting
    sending = 0
    for row in rows:
        if row.stage == PlanStage.WAITING:
            return row if sending <= AHEAD else None
        sending += row.stage == PlanStage.SENDING
    return None


async def _turn(
    session: AsyncSession,
    gates: Gates,
    plan: SalesPlanModel,
    row: SalesPlanHypothesisModel,
    spent: dict[int, Spent],
    now: datetime,
) -> Turn:
    """Ход гипотезы: итог её прогона, числа, переходы — и продолжение или новый прогон."""
    locked = await _locked(session, Row, row.id)
    if locked is None or locked.stage not in (PlanStage.WAITING, PlanStage.COLLECTING):
        return NOTHING
    if row.stage == PlanStage.WAITING:
        move(row, PlanStage.COLLECTING, now, why=TURN_CAME)
    latest = await session.scalar(
        select(Run)
        .where(Run.plan_hypothesis_id == row.id)
        .order_by(Run.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    fresh = await _settled(session, row, latest)
    await count(session, row, spent.get(row.id, Spent()), now)
    if row.found >= row.target:
        return move(row, PlanStage.SENDING, now, why=TARGET_MET)
    if latest is not None and latest.stage == Stage.STOPPED:
        return await _stopped(session, gates, plan, row, latest, fresh=fresh, now=now)
    return await new_run(session, gates, plan, row, spent, now)


def _fresh(row: SalesPlanHypothesisModel, run: SalesSegmentRunModel) -> bool:
    """Прогон кончился после последнего взгляда прохода на гипотезу (`plan_settle`, шапка)."""
    return run.finished_at is not None and (
        row.counted_at is None or run.finished_at > row.counted_at
    )


async def _settled(
    session: AsyncSession, row: SalesPlanHypothesisModel, run: SalesSegmentRunModel | None
) -> bool:
    """Итог прогона — в реестр один раз: прогон кончился после последнего взгляда прохода.
    Возвращает, новый ли он для прохода."""
    if run is None or not _fresh(row, run):
        return False
    await settle(session, run, lost_tries=plan_cfg.LOST_TRIES)
    return True


async def _stopped(
    session: AsyncSession,
    gates: Gates,
    plan: SalesPlanModel,
    row: SalesPlanHypothesisModel,
    run: SalesSegmentRunModel,
    *,
    fresh: bool,
    now: datetime,
) -> Turn:
    """Прогон встал: новая остановка — по виду (шапка модуля); временная и уже виденная — тот же
    прогон продолжается."""
    if fresh:
        words = run.error or SILENT_STOP
        trouble(row, Problem.RUN_FAILED, words, run.finished_at or now)
        kind = of_stop(run.error)
        if kind is Kind.STOP:
            return move(row, PlanStage.STOPPED, now, why=words)
        if kind is Kind.PAUSE:
            return pause(plan, words)
    return await resume(session, gates, row, run, now)
