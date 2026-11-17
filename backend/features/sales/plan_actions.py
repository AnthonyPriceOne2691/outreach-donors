"""Кнопки «Плана работ» на вкладке «Гипотезы»: остановить и продолжить гипотезу плана и план,
изменить качество сайтов, «Собрать ещё N», «Добавить ключи».

Этапы и проблемы — те же, что пишет конвейер (`plan_tick.py`, переходы — `plan_moves.py`): кнопка
только ставит строку туда, откуда конвейер продолжит сам. Договор с конвейером:

- **план с паузы снимает только кнопка** — «Продолжить план»: `running`, слова причины уходят.
  «Остановить план» — `paused` своими словами: новых прогонов нет, идущий доходит;
- **«Остановить» гипотезу** — `stopped` и проблема «остановлена кнопкой»: новых прогонов нет, идущий
  доходит (его числа конвейер считает), собранным лидам письма идут (`autobuild`: этап не в счёт).
  Набравшую цель и готовую не останавливают — прогонов у них уже нет;
- **снова в работе** — «Продолжить» гипотезу, «Собрать ещё N», ключи или глубина при «нужны ключи»:
  гипотеза ждёт очереди, а если очередь её (правило прохода «одна вперёд», `plan_tick._next_row`) —
  собирает сразу: собирает одна гипотеза плана. Проблемы «остановлена кнопкой» и «прогон встал»
  снимаются: «Продолжить» значит «попробовать снова» — без паузы повтора (`plan_gates.retry_after`);
  встал её прогон — конвейер продолжит тот же прогон и выдачу второй раз не купит. План, окончённый
  целиком, снова идёт: в нём опять есть работа. План на паузе так и стоит — его снимает своя кнопка;
- **ключей и глубины нет** (`plan_waves.next_batch`) — «Продолжить» отказывает словами «нужны
  ключи», «Собрать ещё N» ставит цель и сразу — `stopped` с «нужны ключи», без прохода конвейера;
- **качество** — глубина и цель — в строке гипотезы плана, страны компаний — правкой гипотезы
  (`hypotheses.edit`, её журнал и правила); действует на новые прогоны и суды. То же самое — не
  правка;
- **«Добавить ключи»** — новые строки реестра на каждом рынке листа, той же чисткой, что ключи книги
  (`plan_keys.py`), вслед за ключами листа; уже известные пропускаются.

**Две кнопки сразу** — строки плана и гипотезы плана под `FOR UPDATE`, сперва план, как у прохода;
проход занятые пропускает (`SKIP LOCKED`). Ждать замка — не дольше `hypotheses.LOCK_WAIT`, дольше —
отказ словами. Состояние читается после замка: вторая из двух одинаковых кнопок видит сделанное
первой и отказывает словами, «Собрать ещё N» дважды — прибавляет дважды.

Каждое действие — строка журнала с автором (`sales_plan:N`, «действие» словами). Запись — в
транзакции вызывающего, коммит — за ним (маршрут).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.sales import hypotheses
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_budget import Problem, keys_words
from backend.features.sales.plan_card import MAX_TARGET
from backend.features.sales.plan_estimate import TOP_WORDS
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import move, pause, solved, trouble
from backend.features.sales.plan_tick import TURN_CAME, _next_row
from backend.features.sales.plan_waves import STEPS, NeedKeys, next_batch
from backend.features.sales.segment_spend import number

logger = logging.getLogger(__name__)

Row = SalesPlanHypothesisModel
Plan = SalesPlanModel

NO_PLAN = "плана №{plan_id} нет — обновите список планов"
NO_ROW = "гипотезы плана №{row_id} нет — обновите список планов"
BUSY = (
    "план сейчас меняет проход конвейера или другая кнопка — повторите через минуту: сделанное ими "
    "будет видно"
)
#: Слова проблемы и паузы, которые ставит кнопка.
STOPPED_BY_HAND = "остановлена кнопкой «Остановить»: новых прогонов нет, идущий доходит"
PLAN_STOPPED = "план остановлен кнопкой «Остановить план»: новых прогонов нет, идущий доходит"
#: Почему этап сменился — полем журнала процесса (`plan_moves.move`).
BY_HAND = "кнопка вкладки «Гипотезы»"
NOTHING_TO_EDIT = "нечего менять: дайте глубину, цель или страны компаний"
#: «Действие» записей журнала — под общей «Правкой», как у заведения плана.
ROW_STOPPED = "гипотеза плана остановлена"
ROW_RESUMED = "гипотеза плана продолжена"
PLAN_PAUSED = "план работ остановлен"
PLAN_RESUMED = "план работ продолжен"
QUALITY = "глубина и цель гипотезы плана поправлены"
MORE = "гипотеза плана собирает ещё"
#: Проблемы, которые снимает гипотеза, снова пущенная в работу: «попробовать снова».
RETRIED = frozenset({Problem.STOPPED, Problem.RUN_FAILED})


class PlanActionError(ValueError):
    """Кнопка ничего не сделала: текст называет почему и что сделать."""


class UnknownPlanError(PlanActionError):
    """Плана или гипотезы плана с таким номером нет (404)."""


class PlanAskError(PlanActionError):
    """Просьба не годится: нечего менять, цель через край, ключей нет (400)."""


class PlanStateError(PlanActionError):
    """Отказ по состоянию: уже остановлена, план окончен, строку держит другая кнопка (409)."""


@dataclass(frozen=True, slots=True)
class Held:
    """План и гипотеза плана под замком и имя её гипотезы продаж — для слов и журнала."""

    plan: SalesPlanModel
    row: SalesPlanHypothesisModel
    name: str


async def _locked[M: (SalesPlanModel, SalesPlanHypothesisModel)](
    session: AsyncSession, model: type[M], found_id: int
) -> M | None:
    """Строка под `FOR UPDATE` в точке сохранения: не дождались замка — отказ словами, а транзакция
    вызывающего жива. Значения — из базы, а не из памяти сессии."""
    await session.execute(text(hypotheses.LOCK_WAIT))
    query = (
        select(model)
        .filter_by(id=found_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    try:
        async with session.begin_nested():
            found: M | None = await session.scalar(query)
    except DBAPIError as exc:
        if hypotheses._state(exc) != hypotheses.LOCK_NOT_AVAILABLE:
            raise
        logger.warning("план работ: строку держит другая кнопка", extra={"id": found_id})
        raise PlanStateError(BUSY) from exc
    return found


async def _plan(session: AsyncSession, plan_id: int) -> SalesPlanModel:
    found = await _locked(session, Plan, plan_id)
    if found is None:
        raise UnknownPlanError(NO_PLAN.format(plan_id=plan_id))
    return found


async def hold(session: AsyncSession, row_id: int) -> Held:
    """Гипотеза плана и её план под замком — сперва план, как у прохода конвейера."""
    plan_id = await session.scalar(select(Row.plan_id).where(Row.id == row_id))
    if plan_id is None:
        raise UnknownPlanError(NO_ROW.format(row_id=row_id))
    plan = await _plan(session, plan_id)
    row = await _locked(session, Row, row_id)
    if row is None:
        raise UnknownPlanError(NO_ROW.format(row_id=row_id))
    name = await session.scalar(
        select(SalesHypothesisModel.name).where(SalesHypothesisModel.id == row.hypothesis_id)
    )
    return Held(plan, row, name or row.sheet)


async def journal(
    session: AsyncSession, plan: SalesPlanModel, author_id: int | None, details: dict[str, Any]
) -> None:
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"sales_plan:{plan.id}",
        details={"план": plan.name, **details},
        stage=Stage.SALES,
    )


def _has(row: SalesPlanHypothesisModel, code: Problem) -> bool:
    return any(item.get("code") == code for item in row.problems)


async def _no_keys(session: AsyncSession, row: SalesPlanHypothesisModel) -> bool:
    """Ключи и глубина кончились: пачки нет ни в одной волне (`plan_waves.next_batch`)."""
    return isinstance(await next_batch(session, row, keys_per_run=1), NeedKeys)


def _keys_trouble(row: SalesPlanHypothesisModel, now: datetime) -> None:
    trouble(row, Problem.KEYS, keys_words(row.max_pages, row.found, row.target), now)


async def _to_work(session: AsyncSession, held: Held, now: datetime) -> None:
    """Гипотеза снова в работе (шапка модуля): ждёт очереди или собирает, если очередь её."""
    plan, row = held.plan, held.row
    if row.stage in (PlanStage.WAITING, PlanStage.COLLECTING):
        return
    solved(row, RETRIED)
    move(row, PlanStage.WAITING, now, why=BY_HAND)
    found = await session.scalars(
        select(Row).where(Row.plan_id == plan.id, Row.id != row.id).order_by(Row.position)
    )
    rows = sorted([*found.all(), row], key=lambda item: item.position)
    if _next_row(rows) is row:
        move(row, PlanStage.COLLECTING, now, why=TURN_CAME)
    if plan.status == PlanStatus.DONE:
        plan.status, plan.finished_at = PlanStatus.RUNNING.value, None
        logger.info("план работ: окончённый план снова идёт", extra={"plan_id": plan.id})


async def keys_back(session: AsyncSession, held: Held, now: datetime) -> None:
    """Гипотеза стояла «нужны ключи»: ключи или глубина прибавились — снова в работе; нет — слова
    проблемы с новыми числами."""
    row = held.row
    if not _has(row, Problem.KEYS):
        return
    if await _no_keys(session, row):
        _keys_trouble(row, now)
        return
    solved(row, frozenset({Problem.KEYS}))
    if row.stage == PlanStage.STOPPED:
        await _to_work(session, held, now)


async def stop_hypothesis(
    session: AsyncSession, row_id: int, *, author_id: int | None, now: datetime
) -> SalesPlanHypothesisModel:
    """«Остановить» гипотезу плана (шапка модуля)."""
    held = await hold(session, row_id)
    row, name = held.row, held.name
    refusal = {
        PlanStage.STOPPED: f"гипотеза «{name}» уже остановлена",
        PlanStage.SENDING: f"гипотеза «{name}» уже набрала цель — новых прогонов у неё нет, а "
        "письма собранным лидам идут",
        PlanStage.DONE: f"гипотеза «{name}» готова — первые письма ушли, останавливать нечего",
    }.get(PlanStage(row.stage))
    if refusal is not None:
        raise PlanStateError(refusal)
    trouble(row, Problem.STOPPED, STOPPED_BY_HAND, now)
    move(row, PlanStage.STOPPED, now, why=BY_HAND)
    await journal(session, held.plan, author_id, {"действие": ROW_STOPPED, "гипотеза": name})
    return row


async def resume_hypothesis(
    session: AsyncSession, row_id: int, *, author_id: int | None, now: datetime
) -> SalesPlanHypothesisModel:
    """«Продолжить» остановленную гипотезу плана (шапка модуля). Цель не набрана, а ключей и
    глубины нет — отказ словами «нужны ключи»: продолжать нечем."""
    held = await hold(session, row_id)
    row, name = held.row, held.name
    if row.stage != PlanStage.STOPPED:
        raise PlanStateError(f"гипотеза «{name}» не остановлена — продолжать нечего")
    if row.found < row.target and await _no_keys(session, row):
        raise PlanStateError(
            f"гипотеза «{name}»: " + keys_words(row.max_pages, row.found, row.target)
        )
    solved(row, frozenset({Problem.KEYS}))
    await _to_work(session, held, now)
    await journal(session, held.plan, author_id, {"действие": ROW_RESUMED, "гипотеза": name})
    return row


async def stop_plan(
    session: AsyncSession, plan_id: int, *, author_id: int | None
) -> SalesPlanModel:
    """«Остановить план»: `paused` своими словами (шапка модуля)."""
    plan = await _plan(session, plan_id)
    if plan.status == PlanStatus.DONE:
        raise PlanStateError(f"план «{plan.name}» окончен — останавливать нечего")
    if plan.status == PlanStatus.PAUSED:
        raise PlanStateError(f"план «{plan.name}» уже стоит: {plan.problem or PLAN_STOPPED}")
    pause(plan, PLAN_STOPPED)
    await journal(session, plan, author_id, {"действие": PLAN_PAUSED})
    return plan


async def resume_plan(
    session: AsyncSession, plan_id: int, *, author_id: int | None
) -> SalesPlanModel:
    """«Продолжить план»: `running`, слова причины уходят — с паузы план снимает только кнопка."""
    plan = await _plan(session, plan_id)
    if plan.status == PlanStatus.RUNNING:
        raise PlanStateError(f"план «{plan.name}» уже идёт")
    if plan.status == PlanStatus.DONE:
        raise PlanStateError(
            f"план «{plan.name}» окончен: все гипотезы готовы или остановлены — продолжите "
            "гипотезу или соберите ещё"
        )
    was = plan.problem
    plan.status, plan.problem = PlanStatus.RUNNING.value, None
    logger.info("план работ: план продолжен", extra={"plan_id": plan.id})
    await journal(
        session, plan, author_id, {"действие": PLAN_RESUMED, **({"стоял": was} if was else {})}
    )
    return plan


def _depth(pages: int, target: int) -> str:
    return f"глубина до {TOP_WORDS.get(pages, pages)}, цель {number(target)}"


@dataclass(frozen=True, slots=True)
class Quality:
    """Изменить качество сайтов: что дано; `None` — поле не трогать."""

    max_pages: int | None = None
    target: int | None = None
    other_countries: str | None = None
    excluded_countries: list[str] | None = None

    def refusal(self) -> str | None:
        """Почему просьба не годится — словами; годится — `None`."""
        if self == Quality():
            return NOTHING_TO_EDIT
        if self.max_pages is not None and self.max_pages not in STEPS:
            return f"глубина — одна из: {', '.join(TOP_WORDS[pages] for pages in STEPS)}"
        if self.target is not None and not 1 <= self.target <= MAX_TARGET:
            return f"цель — от 1 до {number(MAX_TARGET)} сайтов"
        return None

    @property
    def countries(self) -> bool:
        """Дано ли поле «страны компаний» — хоть одна часть."""
        return self.other_countries is not None or self.excluded_countries is not None


async def change_quality(
    session: AsyncSession, row_id: int, ask: Quality, *, author_id: int | None, now: datetime
) -> SalesPlanHypothesisModel:
    """Глубина и цель — строка гипотезы плана, страны компаний — правкой гипотезы (шапка модуля).
    Глубже при «нужны ключи» — снова в работе."""
    refusal = ask.refusal()
    if refusal is not None:
        raise PlanAskError(refusal)
    held = await hold(session, row_id)
    row = held.row
    if ask.countries:
        await hypotheses.edit(
            session,
            row.hypothesis_id,
            other_countries=ask.other_countries,
            excluded_countries=ask.excluded_countries,
            author_id=author_id,
        )
    was = (row.max_pages, row.target)
    row.max_pages, row.target = ask.max_pages or row.max_pages, ask.target or row.target
    if (row.max_pages, row.target) == was:
        return row
    await keys_back(session, held, now)
    await journal(
        session,
        held.plan,
        author_id,
        {
            "действие": QUALITY,
            "гипотеза": held.name,
            "было": _depth(*was),
            "стало": _depth(row.max_pages, row.target),
        },
    )
    return row


async def collect_more(
    session: AsyncSession, row_id: int, more: int, *, author_id: int | None, now: datetime
) -> SalesPlanHypothesisModel:
    """«Собрать ещё N»: цель `+N` и гипотеза снова в работе; ключей и глубины нет — сразу
    `stopped` и «нужны ключи» (шапка модуля)."""
    held = await hold(session, row_id)
    row = held.row
    if more < 1 or row.target + more > MAX_TARGET:
        raise PlanAskError(
            f"цель гипотезы «{held.name}» — от 1 до {number(MAX_TARGET)}: сейчас "
            f"{number(row.target)}, прибавить можно до {number(MAX_TARGET - row.target)}"
        )
    was = row.target
    row.target += more
    if await _no_keys(session, row):
        _keys_trouble(row, now)
        if row.stage != PlanStage.STOPPED:
            move(row, PlanStage.STOPPED, now, why=BY_HAND)
    else:
        solved(row, frozenset({Problem.KEYS}))
        await _to_work(session, held, now)
    await journal(
        session,
        held.plan,
        author_id,
        {"действие": MORE, "гипотеза": held.name, "было": was, "стало": row.target},
    )
    return row
