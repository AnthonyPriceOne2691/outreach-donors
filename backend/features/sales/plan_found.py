"""«Найдено» гипотезы плана работ: один счёт у прохода конвейера и у решений человека.

«Найдено» — уникальные сайты в полосе «принят» у прогонов гипотезы плана (`found_sites`). Полосу
здесь не решают, а читают: её кладут судья по режиму (`segment_judge.effective`) и человек. Число
хранится в строке гипотезы плана (`SalesPlanHypothesisModel.found`): экран и API плана не
пересчитывают сотни прогонов на каждом опросе. Пишут его трое, одним счётом (`recount_row`):

- **проход конвейера** (`plan_tick`) — у идущего прогона, у гипотезы, чей ход, и у гипотез в
  письмах — вместе с расходом (`plan_settle.count`); у гипотез в конце идущего плана — только его;
- **решение человека, которое меняет полосу сайта в прогоне плана** (`recount`) — сразу, в той же
  транзакции: «Принять» и «Отказ» (`segment_report.decide`), «Отменить решение»
  (`segment_undo.undo`), пересуд прогона (`segment_rejudge`). Стенд 19.11: человек принял сайт и
  отменил решение, а экран и API плана показывали прежнее число до следующего прохода, у
  остановленной гипотезы — насовсем: проход её не считал. Заведение лида полосу не меняет и не
  пересчитывает;
- **кнопка строки плана** (`plan_actions.hold`) — под своим замком, до решения: «Продолжить»,
  «Собрать ещё», «Качество сайтов» и «Добавить ключи» судят по живому числу. Стенд 20.11:
  пропущенный под замком пересчёт у окончённого плана и плана на паузе не досчитывал никто — проход
  берёт только идущие планы, — и «Продолжить» по старому числу отказывала «нужны ключи», когда
  сайтов уже хватало.

**Слова «нужны ключи» следуют за числом** (`plan_moves.restate_keys`): «…найдено 24 из 100 —
добавьте ключи» — снимок на момент остановки; цель набрана — проблема снимается. Импорт — при
вызове: `plan_moves` стоит на `plan_budget`, а тот через `segment_watch` и задачи прогона грузит
решения человека (`segment_undo`, `segment_rejudge`), которые зовут этот модуль.

**Строка гипотезы плана — под замком до конца транзакции (`FOR UPDATE`), счёт — после замка.** Два
решения по сайтам одной гипотезы плана считают по очереди, и второе видит первое: счёт внутри
одного `UPDATE` смотрел бы на базу до ожидания замка и затёр бы чужое решение своим старым числом.
Проход конвейера берёт строку `SKIP LOCKED` — занятую пропускает до следующего раза.

**Решение человека важнее свежего числа: пересчёт его не держит и не роняет.** Строку ждём не дольше
`LOCK_WAIT` (полторы секунды) в точке сохранения: дольше её держит проход, который ставит прогон и
спрашивает смету, или кнопка плана. Не дождались — пересчёт пропущен строкой журнала, решение и
транзакция вызывающего живы, а число досчитает проход идущего плана (`SALES_PLAN_PASS_SEC`) или
первая кнопка строки. Предел — `SET LOCAL`, он дожил бы до конца транзакции вызывающего: после
замка возвращается прежний (у отмены — свой `segment_undo.LOCK_WAIT`, у решения и пересуда — без
предела), а отказ по пределу откатывает его вместе с точкой сохранения.

**Пересчёт трогает только «найдено» и слова «нужны ключи»**: время счёта (`counted_at`) — взгляд
прохода, по нему проход узнаёт прогон, кончившийся после его последнего взгляда
(`plan_tick._fresh`), и отдаёт его итог в реестр ключей; расход и резерв считает проход.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)

logger = logging.getLogger(__name__)

Row = SalesPlanHypothesisModel
Run = SalesSegmentRunModel
Candidate = SalesSegmentCandidateModel

#: Сколько пересчёт ждёт строку гипотезы плана (шапка модуля). Строкой целиком — значение не из
#: ввода; тест подменяет её короткой.
LOCK_WAIT = "SET LOCAL lock_timeout = '1500ms'"
#: SQLSTATE «замок не дождались» (`lock_not_available`).
LOCK_NOT_AVAILABLE = "55P03"


async def found_sites(session: AsyncSession, plan_hypothesis_id: int) -> int:
    """«Найдено» гипотезы плана — уникальные сайты в полосе «принят» у её прогонов, одним
    запросом (шапка модуля)."""
    found = await session.scalar(
        select(func.count(func.distinct(Candidate.domain_id)))
        .select_from(Candidate)
        .join(Run, Run.id == Candidate.run_id)
        .where(Run.plan_hypothesis_id == plan_hypothesis_id, Candidate.lane == Lane.ACCEPT)
    )
    return int(found or 0)


async def recount_row(session: AsyncSession, row: Row) -> int:
    """«Найдено» строки гипотезы плана, которую вызывающий держит под замком, — заново, и слова
    «нужны ключи» — на новое число (шапка модуля). Возвращает прежнее число. Запись — в сессии
    вызывающего, коммит — за ним."""
    from backend.features.sales.plan_moves import restate_keys  # noqa: PLC0415 — круг импорта

    was, row.found = row.found, await found_sites(session, row.id)
    restate_keys(row)
    return was


async def recount(session: AsyncSession, run_id: int) -> int | None:
    """«Найдено» гипотезы плана, чей прогон `run_id`, — заново, после решения человека по его сайту:
    строка под замком, потом счёт (шапка модуля). Прогон не плана или строку не дождались —
    ничего (`None`). Запись — в сессии вызывающего, коммит — за ним."""
    row_id = await session.scalar(select(Run.plan_hypothesis_id).where(Run.id == run_id))
    if row_id is None:
        return None
    row = await _held(session, row_id)
    if row is None:
        logger.info(
            "план работ: «найдено» не пересчитано — строку гипотезы плана держит проход или "
            "кнопка; досчитает проход идущего плана или первая кнопка строки",
            extra={"plan_hypothesis_id": row_id, "run": run_id},
        )
        return None
    was = await recount_row(session, row)
    await session.flush()
    if row.found != was:
        logger.info(
            "план работ: «найдено» пересчитано по решению человека",
            extra={"plan_hypothesis_id": row.id, "run": run_id, "was": was, "found": row.found},
        )
    return row.found


def _sqlstate(exc: DBAPIError) -> str:
    """SQLSTATE отказа базы — как его читает общий обработчик (`api/errors._database_code`)."""
    orig = exc.orig
    return str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "")


async def _held(session: AsyncSession, row_id: int) -> Row | None:
    """Строка гипотезы плана под `FOR UPDATE` — не дольше `LOCK_WAIT`, в точке сохранения, с
    прежним пределом вызывающего после замка (шапка модуля); не дождались — `None`."""
    was = await session.scalar(text("SELECT current_setting('lock_timeout')"))
    query = (
        select(Row)
        .where(Row.id == row_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    try:
        async with session.begin_nested():
            await session.execute(text(LOCK_WAIT))
            row = await session.scalar(query)
            await session.execute(
                text("SELECT set_config('lock_timeout', :was, true)"), {"was": was}
            )
    except DBAPIError as exc:
        if _sqlstate(exc) != LOCK_NOT_AVAILABLE:
            raise
        return None
    return row
