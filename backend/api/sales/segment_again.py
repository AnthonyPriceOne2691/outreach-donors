"""«Повторить сейчас» из отчёта прогона сбора: `POST /sales/segment/runs/{N}/again`.

Разбор прогона сбора 20.10: поиск адреса сайтам, чей срок повтора настал (а «не спросили» — при
платной ступени), или очистка ждущих лидов прогона — проходом без выдачи и судьи под номером
прогона (`segment_run.again`), тем же путём постановки, что «Продолжить» (`segment.queued_pass`).
Срок, который ещё не настал, кнопка не трогает (ревью 20.10: нажатия сжигали расписание). Кого
возьмёт и до скольких платных запросов — то же число, что рядом с кнопкой
(`features/sales/segment_waits.offer`), и оно же — в журнале. Повторять нечего — 409 словами;
прогон или пересуд идёт — 409 словами. Сам, по сроку, повтор адреса ставит очередь
(`sales/segment_again.py`), а лидов «Hunter ещё проверяет» перепроверяет без кнопки
`sales/verify_again.py`.

**Права** — `sales` и `run`, как у «Продолжить»: повтор тратит (поиск адреса, проверка).

Своим модулем: маршруты прогона (`segment.py`) стоят у предела длины файла.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales import segment as runs
from backend.api.sales.segment_schemas import RunQueued
from backend.api.sales.segment_sites import refuse_while_rejudging
from backend.api.sales.segment_waits import AgainBody
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_run import Again
from backend.features.sales.segment_waits import offer
from backend.features.sales.verifier import live

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))

#: «Повторить сейчас», а повторять нечего — по видам повтора.
NOTHING_AGAIN = {
    Again.ADDRESSES: (
        "Повторять нечего: сайтов, чей срок повтора поиска адреса настал, в прогоне нет — "
        "у остальных повтор пойдёт сам в свой срок"
    ),
    Again.CLEANING: (
        "Повторять нечего: лидов прогона, которых возьмёт очистка сейчас, нет — у остальных "
        "перепроверка пойдёт сама в свой срок"
    ),
}


@router.post(
    "/segment/runs/{run_id}/again",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Повторить сейчас: поиск адреса, чей срок настал, или очистку ждущих лидов",
)
async def again_run(
    run_id: int,
    body: AgainBody,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """То, что ждёт повтора и чей срок настал, — сейчас: проходом без выдачи и судьи под номером
    прогона. Кого он возьмёт и до скольких платных запросов — тем же условием, что у числа рядом
    с кнопкой (`segment_waits.offer`). Повторять нечего — 409 словами; прогон или пересуд идёт —
    409 словами."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, runs.UNKNOWN_RUN.format(run_id=run_id))
    what = Again(body.what)
    await refuse_while_rejudging(run.id, runs.bulk_queue())
    found = await offer(session, run, what.value, real=live(), now=datetime.now(UTC))
    if found.count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_AGAIN[what])
    job = await runs.queued_pass(run.id, what.value)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, runs.RUN_GOING)
    run.job_id, run.finished_at = str(job.id), None
    what_words = "поиск адреса" if what is Again.ADDRESSES else "очистка лидов"
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": f"повтор сбора базы продаж: {what_words}",
            "прогон": run.id,
            "возьмёт": found.count,
            "платных запросов Hunter до": found.paid,
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))
