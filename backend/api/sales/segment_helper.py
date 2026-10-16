"""Помощник описания сегмента: `POST /sales/segment/helper` — черновик описания → предложение.

Ответ — предложение на экране: описание для судьи сегмента, вопросы человеку и «черновик
расплывчатый» (`features/sales/segment_helper.py`). В гипотезу оно не пишется: человек берёт
текст, правит и сохраняет сам — окном гипотезы или правкой описания на вкладке сбора. Зовут
помощника кнопка «Помочь с описанием» у поля описания и проверка описания при смете сбора.

**Право — `sales`**, как у правки описания: помощник — часть того, как описание пишется, и кто
может править описание (телемаркетолог тоже), может и спросить помощника. Трата — доли цента за
вызов (модель судьи сегмента, усилие low) под дневным потолком продаж, а тот же черновик второй
раз не оплачивается — кэш. Траты на сотни сайтов — запуск сбора и пересуд — остаются за `run`.

**Отказы словами:** черновика нет — 422; потолок модели выбран — 429 (общий отказ
`LlmCapExceededError`, до вызова); модель не ответила или ответ негоден — 502, токены ответа,
если он был, уже в журнале расхода.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.segment_schemas import SEGMENT_LENGTH
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import usage_cap
from backend.features.sales.segment_helper import EmptyDraftError, HelperFailedError, help_draft

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))


class HelpBody(BaseModel):
    """Черновик описания — тот, что в поле на экране, сохранён он или нет."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=SEGMENT_LENGTH)


class HelpView(BaseModel):
    """Предложение помощника: описание для судьи, вопросы человеку, что в черновике собьёт судью
    и расплывчат ли черновик (бед больше нуля)."""

    description: str
    unclear: list[str]
    problems: list[str]
    loose: bool
    #: Из кэша — модель не звали, денег не стоило.
    cached: bool


@router.post("/segment/helper", response_model=HelpView, summary="Помощник описания сегмента")
async def help_segment(
    body: HelpBody,
    session: AsyncSession = Depends(db_session),
    _: UserModel = _seller,
) -> HelpView:
    """Предложение по черновику: из кэша или вызовом модели под потолком продаж."""
    try:
        found = await help_draft(session, body.description, own=usage_cap.sales_cap())
    except EmptyDraftError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except HelperFailedError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return HelpView(
        description=found.description,
        unclear=list(found.unclear),
        problems=list(found.problems),
        loose=found.loose,
        cached=found.cached,
    )
