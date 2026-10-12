"""Правка лида с экрана «Лиды»: `PATCH /sales/leads/{id}` — под правами `sales` и `send`.

Имя, должность, компания, язык, страна и пояс — только присланные поля; пустое или `null` —
«не задано». Правила полей, журнал и признак пересборки письма — `features/sales/lead_edit.py`:
экран зовёт то же, что позвала бы консоль. **Право — как у прочих правок модуля** (цепочка,
база знаний, отправитель): имя и компания уходят в письмо, язык и пояс решают, какое письмо и
когда, — менять их может тот, у кого есть право отправки.

Ответ — строка лида той же формы, что в списке, и что изменилось: поля и ждёт ли письмо в
очереди пересборки. Отказы — словами: поле не годится — 400, лида нет — 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.schemas import LeadCard
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import browse, lead_edit

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: После `_seller`: без раздела отказ — о нём.
_sender = Depends(needs(Permission.SEND))


class LeadPatch(BaseModel):
    """Правка лида: только присланные поля; пусто или `null` — «не задано». Лишнее поле —
    отказ схемы: опечатка в имени поля не должна молча ничего не менять."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    position: str | None = None
    company: str | None = None
    language: str | None = None
    country: str | None = None
    timezone: str | None = None


class LeadChanged(LeadCard):
    """Лид после правки и что изменилось."""

    #: Поля, которые правка изменила; пусто — всё было так же, журнал не тронут.
    changed: list[str]
    #: Первое письмо лида ждёт в очереди — следующая сборка соберёт его заново.
    rebuild: bool


@router.patch("/leads/{lead_id}", response_model=LeadChanged, summary="Поправить лида")
async def change_lead(
    lead_id: int,
    body: LeadPatch,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> LeadChanged:
    try:
        done = await lead_edit.change(
            session, lead_id, body.model_dump(exclude_unset=True), author_id=author.id
        )
    except lead_edit.UnknownLeadError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except lead_edit.LeadEditError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    row = await browse.lead(session, lead_id)
    assert row is not None  # лид только что поправлен; лидов удаляет только чистка проб
    card = LeadCard.of(row).model_dump()
    return LeadChanged(**card, changed=done.fields, rebuild=done.rebuild)
