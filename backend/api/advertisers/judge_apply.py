"""«Применить советы судьи»: итог очереди, план полосы, применение порцией, партии и откат.

Советы судьи рекламодателей ложатся решениями по спорным парам (решение владельца 10.11.2026,
`crawl/judge_apply.py`): «не пишем» и «пишем» — полосами, каждая после показа плана; «не понять»
остаётся человеку.

**Права.** Итог очереди и список партий — под `view`: это числа. План с примерами, применение и
откат — под `run` и `prices` разом. `run` — право запуска «Переобойти спорные»: слабее массовое
решение быть не может. `prices` — право одного решения «Пишем / Не пишем»: учётка, которой его
сняли, не должна решать сотни пар одной кнопкой. План — под тем же правом, что применение: в нём
советы нерешённым парам, а тому, кто решает руками, совет до решения не показывают
(`crawl/judge_advice.py`).

**Порция — запрос.** Экран повторяет применение, пока не останется ноль: порция фиксируется целиком
вместе со своей записью журнала, и обрыв теряет только летевшую. Первая порция начинает партию —
имя даёт сервер; следующие приходят с ним. Откатанная партия не продолжается.

**Чтение фиксируется тоже.** Перед отбором советы, поставленные прежней таблицей правил, ставятся
заново нынешней (`judge_store.relabel`): без фиксации этот пересчёт повторял бы каждый запрос.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.judge_apply_schemas import (
    AppliedView,
    ApplyBody,
    BatchView,
    PlanView,
    RevertedView,
    StandingView,
)
from backend.api.deps import db_session, needs
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import judge_apply, judge_batches, judge_plan, judge_revert
from backend.features.crawl.judge_apply import ANSWERED_NAMED, Band
from backend.features.crawl.judge_mark import BATCH

router = APIRouter(prefix="/judge-apply")

_viewer = Depends(needs(Permission.VIEW))

NOTHING_TO_APPLY = "Применять нечего: пар с таким советом судьи в очереди нет"
REVERTED = "Партия откатана и не продолжается: начните новую"


async def _applier(
    user: UserModel = Depends(needs(Permission.RUN)),
    _decides: UserModel = Depends(needs(Permission.PRICES)),
) -> UserModel:
    """Кто применяет и откатывает: право запуска и право решения разом (шапка модуля)."""
    return user


@router.get("", response_model=StandingView, summary="Советы судьи очереди спорных — числами")
async def read_standing(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> StandingView:
    """Сколько ждущих пар с каким советом. Решений не пишет."""
    found = await judge_apply.standing(session)
    await session.commit()
    return StandingView.of(found)


@router.get("/plan", response_model=PlanView, summary="План полосы: кого и что это тронет")
async def read_plan(
    band: Band = Query(description="полоса: не пишем (skip) или пишем (write)"),
    _: UserModel = Depends(_applier),
    session: AsyncSession = Depends(db_session),
) -> PlanView:
    """Сколько пар и доноров, какими правилами, примеры и последствия. Решений не пишет."""
    found = await judge_plan.plan(session, band)
    await session.commit()
    return PlanView.of(found)


@router.post("", response_model=AppliedView, summary="Применить порцию полосы")
async def apply_advice(
    body: ApplyBody,
    author: UserModel = Depends(_applier),
    session: AsyncSession = Depends(db_session),
) -> AppliedView:
    """Порция решений по советам полосы — тем же путём, что решение человека. Отказ — словами."""
    batch = body.batch or judge_apply.new_batch(body.band)
    try:
        # Имя партии проверяется до журнала: по времени в имени журнал её и ищет.
        if body.batch is not None and judge_apply.band_of(batch) is body.band:
            known = await judge_batches.batch(session, batch)
            if known is not None and known.reverted_at is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, REVERTED)
        result = await judge_apply.apply(session, body.band, batch=batch, limit=body.limit)
    except judge_apply.BatchBandError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.batch is None and not (result.applied or result.decided or result.gone):
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_TO_APPLY)
    if result.applied:
        await AccessRepository(session).record(
            AuditAction.ADVERTISER_JUDGE_APPLIED,
            author_id=author.id,
            target=f"judge_batch:{result.batch}",
            details=judge_batches.portion_details(result),
        )
    await session.commit()
    return AppliedView.of(result, named=ANSWERED_NAMED)


@router.get("/batches", response_model=list[BatchView], summary="Последние партии")
async def read_batches(
    limit: int = Query(
        default=judge_batches.BATCHES, ge=1, le=judge_batches.MAX_BATCHES, description="сколько"
    ),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[BatchView]:
    """Кто, когда, какой полосой, сколько применено и откатана ли — новые сверху, не больше
    `limit`: журнал растёт, а экрану нужны последние."""
    found = await judge_batches.batches(session, limit=limit)
    return [BatchView.of(batch) for batch in found]


@router.post(
    "/batches/{batch}/revert", response_model=RevertedView, summary="Откатить партию в очередь"
)
async def revert_batch(
    batch: str = Path(pattern=BATCH.pattern, max_length=64, description="имя партии"),
    author: UserModel = Depends(_applier),
    session: AsyncSession = Depends(db_session),
) -> RevertedView:
    """Решения партии, которые не менял человек, — обратно в очередь. Вернуть нечего — 409."""
    try:
        band = judge_apply.band_of(batch)
        result = await judge_revert.revert(session, batch)
    except judge_apply.BatchBandError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except judge_revert.NothingBackError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_JUDGE_REVERTED,
        author_id=author.id,
        target=f"judge_batch:{batch}",
        details=judge_batches.revert_details(result, band.value),
    )
    await session.commit()
    return RevertedView.of(result)
