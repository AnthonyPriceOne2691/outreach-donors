"""Ручная очередь форм рекламодателей: строка ТЗ «только форма, нет email — заполняем
вручную» на Этапе 2 (сверка ТЗ 11.10.2026).

Очередь была только у доноров (`api/contacts/routes.py`), и рекламодатель, у которого
лестница нашла одну форму, оффера не получал никогда. Здесь — то же для рекламодателей:
страница очереди, «форму заполнили» с адресом и «не вышло». Правило очереди и адреса —
`contacts/forms.py` и `contacts/manual.py`, права — как у доноров: смотреть — `view`,
вписывать адрес — `run`; в журнале — кто вписал и откуда адрес.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.contacts.schemas import (
    AdvertiserFormCard,
    AdvertiserFormsView,
    FilledBody,
    GiveUpBody,
)
from backend.api.deps import db_session, needs
from backend.config import contacts as contacts_cfg
from backend.features.access.repository import AccessRepository
from backend.features.contacts import forms
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel

router = APIRouter()

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))


@router.get("/forms", response_model=AdvertiserFormsView, summary="Формы рекламодателей")
async def form_queue(
    page: int = Query(default=1, ge=1, le=1_000_000, description="страница очереди, с единицы"),
    limit: int = Query(
        default=forms.PAGE_SIZE, ge=1, le=forms.MAX_PAGE_SIZE, description="строк на странице"
    ),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AdvertiserFormsView:
    """Рекламодатели, у которых форма вместо адреса, — по странице, как у доноров."""
    rows = await forms.advertiser_queue(session, page=page, size=limit)
    return AdvertiserFormsView(
        rows=[AdvertiserFormCard.of(row) for row in rows],
        total=await forms.advertiser_total(session),
        page=page,
        limit=limit,
        monthly_left=await forms.monthly_left(session),
        monthly_cap=contacts_cfg.MANUAL_QUEUE_MONTHLY_CAP,
        donors_waiting=await forms.total(session),
        retry_days=contacts_cfg.CONTACT_TTL_DAYS,
    )


@router.post(
    "/forms/{advertiser_id}/filled", response_model=AdvertiserFormCard, summary="Форму заполнили"
)
async def filled(
    advertiser_id: int,
    body: FilledBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> AdvertiserFormCard:
    """Рекламодатель дал адрес через форму — дальше ему уходит оффер."""
    row = await forms.advertiser_filled(session, advertiser_id, email=body.email)
    await AccessRepository(session).record(
        AuditAction.CONTACT_ADDED,
        author_id=author.id,
        target=f"advertiser:{advertiser_id}",
        details={
            "рекламодатель": row.host,
            "адрес": body.email.strip().lower(),
            "откуда": "форма, заполнена руками",
        },
    )
    await session.commit()
    return AdvertiserFormCard.of(row)


@router.post(
    "/forms/{advertiser_id}/give-up", response_model=AdvertiserFormCard, summary="Форма не вышла"
)
async def give_up(
    advertiser_id: int,
    body: GiveUpBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> AdvertiserFormCard:
    """Закрыть рекламодателя без адреса: висеть в очереди вечно он не должен."""
    row = await forms.advertiser_gave_up(session, advertiser_id)
    await AccessRepository(session).record(
        AuditAction.CONTACT_ADDED,
        author_id=author.id,
        target=f"advertiser:{advertiser_id}",
        details={"рекламодатель": row.host, "исход": "форма не вышла", "почему": body.reason},
    )
    await session.commit()
    return AdvertiserFormCard.of(row)
