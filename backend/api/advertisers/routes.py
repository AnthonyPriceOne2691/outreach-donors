"""Экран ручной проверки кандидатов в рекламодатели.

**Без этого экрана допуск в десять процентов ложных не держится.**
Скоринг выносит вердикт по пяти признакам, два из которых выведены
из замера на одной нише; пограничные случаи должен смотреть человек,
а не порог. До экрана ту же работу делала консоль — и делала тем же
кодом, порядок живёт в `features/crawl/review.py`.

**Смотреть — под правом `view`, решать — под `prices`.** Право выбрано
не по названию, а по смыслу: `prices` — это уже право «человек
поправляет то, что решила машина», ровно тот же класс действия. Если
окажется, что решать кандидатов должен кто-то другой, меняется одна
строка здесь.

**Решение пишется в журнал.** «Откуда у нас этот адресат» спросит либо
сам адресат, либо юрист.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.schemas import CandidateCard, CandidatesView, DecisionBody
from backend.api.deps import db_session, needs
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import review

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advertisers", tags=["рекламодатели"])

_viewer = Depends(needs(Permission.VIEW))
_reviewer = Depends(needs(Permission.PRICES))


@router.get("", response_model=CandidatesView, summary="Очередь ручной проверки")
async def queue(
    include_decided: bool = Query(
        default=False, description="показывать и те, по которым решение уже принято"
    ),
    limit: int = Query(default=review.PAGE_SIZE, ge=1, le=200),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> CandidatesView:
    """Пограничные кандидаты, которых ещё никто не смотрел."""
    rows = await review.queue(session, limit=limit, include_decided=include_decided)
    return CandidatesView(
        rows=[CandidateCard.of(row) for row in rows],
        waiting=await review.waiting(session),
        counts=await review.counts(session),
    )


@router.post("/{candidate_id}/decide", response_model=CandidateCard, summary="Решение человека")
async def decide(
    candidate_id: int,
    body: DecisionBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> CandidateCard:
    """Подтвердить кандидата или отклонить.

    Балл скоринга при этом не меняется: по расхождению между ним
    и решением человека и считается, как часто скоринг ошибается.
    """
    try:
        row = await review.decide(
            session, candidate_id, confirmed=body.confirmed, by=author.email, force=body.force
        )
    except review.UnknownCandidateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except review.AlreadyDecidedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=author.id,
        target=f"candidate:{candidate_id}",
        details={
            "донор": row.donor_host,
            "рекламодатель": row.target_root,
            "решение": "пишем" if row.confirmed else "не пишем",
            "балл скоринга": row.points,
        },
    )
    await session.commit()
    return CandidateCard.of(row)
