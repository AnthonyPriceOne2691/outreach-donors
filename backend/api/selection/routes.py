"""Экран отбора: кто принят, кто отклонён, кем и почему.

**Смотреть — под правом `view`, решать — под `prices`.** Тот же выбор, что
у спорных рекламодателей, и по той же причине: `prices` — это право
«человек поправляет то, что решила машина». Если решать отбор должен
кто-то другой, меняется одна строка здесь.

**Решение пишется в журнал.** Оно меняет, кому уйдёт письмо, — во
включённом судье прямо, в наблюдении через следующий прогон.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.selection.schemas import DecisionBody, SelectionCard, SelectionView
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.donors.selection import (
    MAX_PAGE_SIZE,
    PAGE_SIZE,
    AnswerFilter,
    HumanFilter,
    JudgeFilter,
    SelectionBrowser,
    SelectionFilters,
    Tab,
    ThresholdsFilter,
    UnknownDomainError,
)

router = APIRouter(prefix="/selection", tags=["отбор"])

_viewer = Depends(needs(Permission.VIEW))
_reviewer = Depends(needs(Permission.PRICES))


class SelectionQuery(BaseModel):
    """Фильтры экрана отбора — одной моделью, по фильтру на колонку таблицы
    (26.09.2026): прежние четыре флага над таблицей стали фильтрами под
    своими колонками.

    Страница — номером, а не сдвигом: экран держит в адресе номер, а размер
    страницы знает только сервер и называет его в ответе."""

    tab: Tab = Field(default=Tab.ACCEPTED, description="принят, к разбору, отклонён")
    search: str | None = Field(default=None, description="по домену или причине")
    thresholds: ThresholdsFilter | None = Field(default=None, description="вердикт порогов")
    judge: JudgeFilter | None = Field(default=None, description="кто вынес вердикт судьи")
    answer: AnswerFilter | None = Field(default=None, description="что ответил донор")
    human: HumanFilter | None = Field(default=None, description="смотрел ли человек")
    page: int = Field(default=1, ge=1, le=1_000_000, description="страница, с единицы")
    limit: int = Field(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="доменов на странице")

    def filters(self) -> SelectionFilters:
        return SelectionFilters(
            tab=self.tab,
            search=self.search,
            thresholds=self.thresholds,
            judge=self.judge,
            answer=self.answer,
            human=self.human,
            page=self.page,
            size=self.limit,
        )


@router.get("", response_model=SelectionView, summary="Отбор по вкладкам")
async def selection(
    query: Annotated[SelectionQuery, Query()],
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> SelectionView:
    browser = SelectionBrowser(session)
    filters = query.filters()
    return SelectionView.of(
        await browser.page(filters),
        await browser.summary(),
        page_number=filters.page,
        limit=filters.size,
    )


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
