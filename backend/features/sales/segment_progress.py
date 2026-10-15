"""Ход прогона сбора: этап с начала работы, «сделано из всего», когда начат и обновлён, пульс.

Тест прогона 14.10: выдача шла шесть минут, а экран стоял «в очереди» без хода — стадия
ставилась после этапа, а не при входе. Человек должен в любой момент видеть, что делает
прогон, сколько осталось и жив ли он (правило видимости отказов).

**Запись хода — `report.progress`**, без миграции; пишет её только сессия прохода — на входе
в этап (`enter`) и на выходе (`advanced`):

- `stage` — этап прохода (`Stage`: `serp`, `judge`, `contacts`, `leads`);
- `done`, `total` — сделано и всего в этапе этого прохода: ключей выдачи, сайтов судьи,
  доменов поиска адреса, лидов; `total` пусто — заранее не известно (лиды);
- `started_at` — когда начат этап, `updated_at` — когда менялась запись хода,
  `pass_started_at` — когда задача взяла проход (с него — «идёт N мин»);
- `mode` — режим покупки выдачи (`SerpMode`), только у этапа выдачи;
- `note` — что человеку знать о сбое этапа до повтора: «выдача упала после оплаты —
  следующий проход купит её заново» (`segment_serp`); вход в этап его снимает.

Ход выдачи «готово X из Y ключей» пишет слушатель покупки (`segment_serp.SerpTrail`).

**Живое «сделано» — при чтении** (`live`): судья и поиск адреса коммитят партиями, и число
идёт из кандидатов, а не из записи — сколько ещё ждут судью, скольким доменам поиск уже
записал исход этого этапа. Писать его по ходу из прохода нельзя без блокировки строки
прогона на всё время сетевой партии.

**Пульс — `updated_at` строки** (`pulse`, своей сессией каждые `PULSE_SEC`), как отметка
о жизни прогона доноров (`runs/lifecycle.heartbeat`): по одному «когда менялась строка»
медленный этап (шесть минут выдачи) неотличим от мёртвого. Пульс трогает только
`updated_at` — отчёт прогона пишет одна сессия, и чужая запись его не затирает.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.features.sales.segment_judging import waiting_judge
from backend.features.sales.segment_models import (
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)

logger = logging.getLogger(__name__)

#: Ключ хода в отчёте прогона.
KEY = "progress"
#: Как часто идущий прогон отмечается живым. Экран называет «обновлено N с назад».
PULSE_SEC = 15.0

Candidate = SalesSegmentCandidateModel
Run = SalesSegmentRunModel

#: Пауза пульса — косвенным именем: тест гасит её здесь, а не сон всего процесса.
_sleep = asyncio.sleep


def _now() -> datetime:
    return datetime.now(UTC)


def _moment(raw: object) -> datetime | None:
    """Время из записи хода; не время — пусто, а не отказ: запись читает экран."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("сбор продаж: время в записи хода не разобрано", extra={"value": raw})
        return None


def entered(
    stage: Stage,
    *,
    total: int | None,
    pass_started: datetime,
    mode: str | None = None,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Запись хода на входе в этап: сделано ноль из `total`."""
    at = (moment or _now()).isoformat()
    found: dict[str, Any] = {
        "stage": stage.value,
        "done": 0,
        "total": total,
        "started_at": at,
        "updated_at": at,
        "pass_started_at": pass_started.isoformat(),
    }
    if mode is not None:
        found["mode"] = mode
    return found


def advanced(run: Run, *, done: int, total: int | None = None) -> dict[str, Any]:
    """Отчёт прогона с новым «сделано» этапа (и «всего», если оно стало известно)."""
    progress = dict(run.report.get(KEY) or {})
    progress["done"] = done
    if total is not None:
        progress["total"] = total
    progress["updated_at"] = _now().isoformat()
    return {**run.report, KEY: progress}


def noted(run: Run, words: str) -> dict[str, Any]:
    """Отчёт прогона с заметкой хода: что будет на следующем проходе — до него."""
    progress = dict(run.report.get(KEY) or {})
    progress["note"] = words
    progress["updated_at"] = _now().isoformat()
    return {**run.report, KEY: progress}


async def enter(
    session: AsyncSession,
    run: Run,
    stage: Stage,
    *,
    total: int | None,
    pass_started: datetime,
    mode: str | None = None,
) -> None:
    """Войти в этап: стадия и запись хода — сразу, своим коммитом, до работы этапа."""
    run.stage = stage
    progress = entered(stage, total=total, pass_started=pass_started, mode=mode)
    run.report = {**run.report, KEY: progress}
    await session.commit()
    logger.info(
        "сбор продаж: этап начат", extra={"run": run.id, "stage": stage.value, "total": total}
    )


async def judge_waiting(session: AsyncSession, run_id: int) -> int:
    """Сколько сайтов прогона ждут судью — тем же правилом, что берёт проход судьи."""
    return int(
        await session.scalar(
            select(func.count()).where(Candidate.run_id == run_id, waiting_judge())
        )
        or 0
    )


async def _walked(session: AsyncSession, run_id: int, since: datetime) -> int:
    """Скольким доменам прогона поиск адреса записал исход с начала этапа."""
    return int(
        await session.scalar(
            select(func.count()).where(
                Candidate.run_id == run_id, Candidate.contact_attempted_at >= since
            )
        )
        or 0
    )


async def live(session: AsyncSession, run: Run) -> dict[str, Any] | None:
    """Ход прогона для экрана: запись прохода и живое «сделано» судьи и поиска адреса.

    У остановленного — тот же счёт: «остановлен на судье, 40 из 60». Записи нет (прогон
    до этой версии или ещё не взят задачей) — `None`."""
    stored = run.report.get(KEY)
    if not isinstance(stored, dict):
        return None
    total = stored.get("total")
    stage = stored.get("stage")
    if stage == Stage.JUDGE and isinstance(total, int):
        return {**stored, "done": max(0, total - await judge_waiting(session, run.id))}
    since = _moment(stored.get("started_at"))
    if stage == Stage.CONTACTS and isinstance(total, int) and since is not None:
        return {**stored, "done": min(total, await _walked(session, run.id, since))}
    return dict(stored)


async def pulse(
    factory: async_sessionmaker[AsyncSession], run_id: int, *, every: float = PULSE_SEC
) -> None:
    """Отмечать прогон живым, пока проход идёт: задача отменяет пульс в конце прохода.

    Своя сессия на удар: строку прогона он трогает одним `updated_at` по часам базы
    (`clock_timestamp` — время удара, а не начала транзакции). Сбой удара — в журнал:
    отметка не должна ронять прогон, ради которого бьётся."""
    while True:
        await _sleep(every)
        try:
            async with factory() as session:
                await session.execute(
                    update(Run).where(Run.id == run_id).values(updated_at=func.clock_timestamp())
                )
                await session.commit()
        except Exception:
            logger.exception(
                "сбор продаж: отметка о жизни прогона не записалась", extra={"run": run_id}
            )
