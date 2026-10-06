"""Жизнь обхода между нажатием и исходом: признак живости и разбор мёртвых.

Правила и пороги — те же, что у прогона Этапа 1 (`runs/lifecycle.py`):
обход разбирается тем же процессом, и разные числа на один вопрос «жив ли»
запутали бы того, кто читает журнал.

- **Живой обход отмечается сам** — удар раз в 30 с своей короткой сессией:
  страница с паузой сайта идёт секунды, и по одной записи пачек медленный
  обход неотличим от мёртвого.
- **Смерть определяет воркер, а не статус задачи**: задача умершего воркера
  остаётся «выполняется» до своего таймаута.
- **«Не знаю» — не «мёртв»**: Redis не ответил — следующий проход спросит снова.

Отличие в том, что считается попыткой. Остановку выкаткой обход переживает
сам: дописывает пачку и ставит себе продолжение (`workers/crawl_jobs.py`), и это
не неудача. Разбор продолжает только умершие обходы — с чекпоинта, не открывая
открытое (`crawl/progress.py`), — до `MAX_RESUMES` раз; дальше «остановлен» и
тревога: третья смерть подряд — уже не случайность, а повод смотреть человеку.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.runs.lifecycle import (
    HEARTBEAT_INTERVAL_SEC,
    MAX_RESUMES,
    RESUME_AFTER_SEC,
    STALE_AFTER_SEC,
    AliveCheck,
    FailureCheck,
    Recovery,
)
from backend.features.runs.reasons import explained_line
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Ключи пометок в отчёте обхода: причина словами и строка исключения как есть.
REASON_KEY = "reason"
FAILURE_KEY = "failure"

#: Поставить обходу задачу заново. Номер задачи или `None` — очередь не ответила.
Enqueue = Callable[[int], str | None]


def _no_failure(_job_id: str | None) -> str | None:
    return None


def noted(run: CrawlRunModel, **fields: Any) -> dict[str, Any]:
    """Отчёт обхода плюс пометки. Пустая пометка не ложится поверх записанной."""
    stats = dict(run.stats or {})
    stats.update({key: value for key, value in fields.items() if value is not None})
    return stats


async def heartbeat(
    factory: async_sessionmaker[AsyncSession], run_id: int, *, interval: float | None = None
) -> None:
    """Отмечать обход живым, пока задачу не отменят.

    Каждый удар — своей сессией: упавший запрос оставляет сессию в сломанной
    транзакции, и все следующие удары молча падали бы за ним. Сбой удара
    только логируется — он не должен ронять обход, ради которого бьётся.
    """
    every = HEARTBEAT_INTERVAL_SEC if interval is None else interval
    while True:
        await asyncio.sleep(every)
        try:
            async with factory() as session:
                await session.execute(
                    update(CrawlRunModel)
                    .where(CrawlRunModel.id == run_id)
                    .values(updated_at=func.now())
                )
                await session.commit()
        except Exception:
            logger.exception("Обход %s: отметка о жизни не записалась", run_id)


#: Так rq называет процесс задачи, убитый снаружи (нехватка памяти, SIGKILL).
#: Общими словами «техническая ошибка» это не объяснить: живой обход, убитый
#: `kill -9`, на экране выглядел именно так.
_KILLED = "Work-horse terminated unexpectedly"


def _cause(failure: FailureCheck, job_id: str | None) -> tuple[str, str | None]:
    """Почему обход молчит: словами для экрана и строкой исключения для журнала."""
    fell = failure(job_id)
    if not fell:
        return "воркер умер", None
    if _KILLED in fell:
        return "процесс задачи убит (нехватка памяти или остановка без предупреждения)", fell
    return f"задача упала: {explained_line(fell)}", fell


def _silent_for(run: CrawlRunModel, moment: datetime) -> float:
    updated = run.updated_at if run.updated_at.tzinfo else run.updated_at.replace(tzinfo=UTC)
    return (moment - updated).total_seconds()


async def recover(
    session: AsyncSession,
    *,
    alive: AliveCheck,
    enqueue: Enqueue,
    failure: FailureCheck = _no_failure,
    now: datetime | None = None,
) -> Recovery:
    """Один проход: продолжить осиротевшие обходы, закрыть безнадёжные.

    Сначала продолжение (через `RESUME_AFTER_SEC` молчания), потом похороны
    (через `STALE_AFTER_SEC`): последняя задача могла успеть начать работу.
    """
    moment = now or datetime.now(UTC)
    resumed: list[int] = []
    stopped: dict[int, str] = {}
    unknown: list[int] = []
    rows = (
        (
            await session.execute(
                select(CrawlRunModel)
                .where(
                    CrawlRunModel.status.in_((CrawlStatus.QUEUED, CrawlStatus.RUNNING)),
                    CrawlRunModel.updated_at < moment - timedelta(seconds=RESUME_AFTER_SEC),
                )
                .order_by(CrawlRunModel.id)
            )
        )
        .scalars()
        .all()
    )
    for run in rows:
        verdict = alive(run.job_id)
        if verdict is None:
            unknown.append(run.id)
            logger.warning(
                "Обход %s: жива ли задача %s — не выяснить, не трогаю", run.id, run.job_id
            )
            continue
        if verdict:
            continue
        said, detail = _cause(failure, run.job_id)
        silent = _silent_for(run, moment)
        if run.resumes < MAX_RESUMES:
            job_id = enqueue(run.id)
            if job_id is None:
                unknown.append(run.id)
                continue
            run.job_id, run.status, run.resumes = job_id, CrawlStatus.QUEUED, run.resumes + 1
            run.stats = noted(
                run, **{REASON_KEY: f"{said}; обход продолжен с чекпоинта", FAILURE_KEY: detail}
            )
            resumed.append(run.id)
            logger.warning(
                "Обход %s (%s) молчит %.0f с — %s, продолжен задачей %s (попытка %s)",
                run.id,
                run.host,
                silent,
                detail or said,
                job_id,
                run.resumes,
            )
            continue
        if silent < STALE_AFTER_SEC:
            continue
        reason = (
            f"остановлен разбором: {said}, продолжений {run.resumes} из {MAX_RESUMES}, "
            f"молчание {silent:.0f} с — смотрит человек"
        )
        run.status, run.finished_at = CrawlStatus.STOPPED, moment
        run.stats = noted(run, **{REASON_KEY: reason, FAILURE_KEY: detail})
        stopped[run.id] = f"обход {run.host} (№{run.id}) {reason}"
        logger.error("Обход %s (%s) закрыт как мёртвый: %s", run.id, run.host, detail or said)

    await session.commit()
    # Тревога — после фиксации: сообщить об остановке, которая откатится, — соврать.
    for text in stopped.values():
        await send_alert(text)
    return Recovery(resumed=resumed, stopped=list(stopped), unknown=unknown)
