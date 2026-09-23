"""Экран отбора: кто принят, кто отклонён, кем и почему.

**Смотреть — под правом `view`, решать — под `prices`.** Тот же выбор, что
у спорных рекламодателей, и по той же причине: `prices` — это право
«человек поправляет то, что решила машина». Если решать отбор должен
кто-то другой, меняется одна строка здесь.

**Решение пишется в журнал.** Оно меняет, кому уйдёт письмо, — во
включённом судье прямо, в наблюдении через следующий прогон.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.selection.schemas import DecisionBody, SelectionCard, SelectionView
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.donors.publisher_judge import Decider
from backend.features.donors.selection import (
    SelectionBrowser,
    SelectionFilters,
    Tab,
    UnknownDomainError,
)

router = APIRouter(prefix="/selection", tags=["отбор"])

_viewer = Depends(needs(Permission.VIEW))
_reviewer = Depends(needs(Permission.PRICES))


def _filters(
    tab: Tab = Query(default=Tab.ACCEPTED, description="принят, к разбору, отклонён"),
    search: str | None = Query(default=None, description="по домену или причине"),
    decided_by: Decider | None = Query(default=None, description="кто вынес вердикт судьи"),
    only_disagreements: bool = Query(default=False, description="человек и машина разошлись"),
    only_unreviewed: bool = Query(default=False, description="человек ещё не смотрел"),
    only_unjudged: bool = Query(default=False, description="судья не смотрел"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SelectionFilters:
    return SelectionFilters(
        tab=tab,
        search=search,
        decided_by=decided_by.value if decided_by else None,
        only_disagreements=only_disagreements,
        only_unreviewed=only_unreviewed,
        only_unjudged=only_unjudged,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=SelectionView, summary="Отбор по вкладкам")
async def selection(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
    filters: SelectionFilters = Depends(_filters),
) -> SelectionView:
    browser = SelectionBrowser(session)
    return SelectionView.of(await browser.page(filters), await browser.summary())


@router.post("/{domain_id}/decide", response_model=SelectionCard, summary="Решение человека")
async def decide(
    domain_id: int,
    body: DecisionBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> SelectionCard:
    """Что это за сайт, по мнению человека.

    Вердикт судьи при этом не меняется: по расхождению между ним и решением
    человека и считается, как часто судья ошибается.
    """
    browser = SelectionBrowser(session)
    try:
        before = await browser.row(domain_id)
        previous = before.domain.human_intent
        row = await browser.decide(domain_id, intent=body.intent, note=body.note)
    except UnknownDomainError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    await AccessRepository(session).record(
        AuditAction.SITE_REVIEWED,
        author_id=author.id,
        target=f"domain:{domain_id}",
        details={
            "домен": row.domain.host,
            "решение": body.intent.value if body.intent else "снято",
            "было": previous,
            "судья": row.domain.judge_recommendation,
            "кто решил у судьи": row.domain.judge_decided_by,
        },
    )
    await session.commit()
    return SelectionCard.of(row)
