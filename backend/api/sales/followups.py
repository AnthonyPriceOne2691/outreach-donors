"""Добивки лида продаж — экрану «Очередь писем»: какими они уйдут.

`GET /sales/leads/{номер}/followups` — без записи, право `sales`. Человек смотрит письма сборки
перед отправкой пачки; тексты добивок готовит та же сборка (`features/sales/followup_texts.py`),
и под строкой письма видно, какими их отправит проход добивок: текст без подписи и адреса (их
допишет отправка) и словами — переписан при сборке (отличие, принятые зоны) или текст шаблона и
почему. Правило выбора текста — то же, что у отправки добивки. Добивки, письмо которых уже есть,
и добивки кончившейся цепочки не впереди — список пуст. Лиду ещё не писали — 404 словами.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.letters.uniqueness import percent_text
from backend.features.sales import followup_texts, mail

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как очередь.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: Связи лида с перепиской нет: сборка ему ещё не писала.
NOT_WRITTEN = "лиду ещё не писали — добивок у него нет: письма собирает сборка очереди"


class FollowupCard(BaseModel):
    """Добивка, какой она уйдёт: шаг шаблона (2 или 3), текст и как он получен — словами."""

    step: int
    #: Текст без подписи и адреса; `null` — шага в цепочке нет, добивка ждёт.
    body: str | None
    words: str
    #: Отличие переписанной при сборке добивки — долей и словами; у шаблона — `null`.
    difference: float | None
    difference_text: str | None
    #: Принятые переписанные зоны; остальные — текст шаблона.
    zones: list[str]

    @classmethod
    def of(cls, found: followup_texts.Planned) -> FollowupCard:
        shown = None if found.difference is None else percent_text(found.difference)
        return cls(
            step=found.step,
            body=found.body,
            words=found.words,
            difference=found.difference,
            difference_text=shown,
            zones=list(found.zones),
        )


class LeadFollowupsView(BaseModel):
    """Добивки лида, которые ещё уйдут, по порядку шагов."""

    lead_id: int
    followups: list[FollowupCard]


@router.get(
    "/leads/{lead_id}/followups",
    response_model=LeadFollowupsView,
    summary="Добивки лида продаж — какими уйдут",
)
async def read_followups(
    lead_id: int,
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> LeadFollowupsView:
    dialog = await mail.lead_dialog(session, lead_id)
    if dialog is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_WRITTEN)
    found = await followup_texts.ahead(session, dialog.link, dialog.lead, dialog.host)
    return LeadFollowupsView(lead_id=lead_id, followups=[FollowupCard.of(item) for item in found])
