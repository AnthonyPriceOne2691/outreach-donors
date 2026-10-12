"""Стоп-лист продаж с экрана: список, вставка списком или файлом, снятие записи.

Раньше список пополняла только консоль (`outreach sales-stoplist-add`), а список текущих
клиентов и партнёров вставляют разом. Логика, правила и журнал — `features/sales/stoplist.py`:
экран и консоль зовут одно и то же, и пополнение снимает лидов, которых закрывает запись,
а снятие записи возвращает их на очистку (`stoplist_leads.py`).

- `GET /sales/stoplist` — записи стоп-листа продаж (кому не пишем, причина, кто и когда) и
  строки общего стоп-листа, которые держат продажи: отписались, пожаловались, общий список
  (`suppressions.holding`, новые первыми, не больше `GENERAL_SHOWN`). Общие здесь только видны:
  снимают их на экране «Стоп-лист».
- `POST /sales/stoplist` — формой `multipart/form-data`: `text` (адреса и домены по строке) или
  `file` (CSV, как у консоли), одно из двух, и `note` — причина словами. Ответ — сколько легло,
  сколько уже было, сколько лидов снято и замечание к каждой строке, которая не легла.
- `POST /sales/stoplist/{id}/remove` — снять запись; лиды, которых закрывала только она,
  возвращаются в «новые».

**Права — как у прочих правок модуля**: смотреть — `sales`, менять — `sales` и `send`: список
решает, кому продажи напишут. Отказы — словами: не прочитано — 400, записи нет — 404.
**Строки общего стоп-листа — ещё и под `view`**, как на общем экране (`api/suppressions`): в них
отписки и жалобы доноров и рекламодателей с адресами. Без `view` их нет ни в списке, ни в числе;
свои записи продаж — как есть.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.features.access.permissions import has_permission
from backend.features.core.domain import Permission, Stage, SuppressionReason
from backend.features.core.models.access import UserModel
from backend.features.sales import stoplist, suppressions
from backend.features.sales.intake import MAX_BYTES
from backend.features.sales.models import SalesStoplistModel

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
#: После `_seller`: без раздела отказ — о нём.
_sender = Depends(needs(Permission.SEND))

#: Строк общего стоп-листа на экране: новые первыми; всего — числом.
GENERAL_SHOWN = 100
NOTHING_GIVEN = "Вставьте адреса и домены по строке или выберите файл — одно из двух"


class StopRow(BaseModel):
    """Запись стоп-листа продаж: домен или адрес, причина, кто и когда внёс."""

    id: int
    host: str | None
    email: str | None
    note: str | None
    created_by: str | None
    created_at: datetime

    @classmethod
    def of(cls, row: SalesStoplistModel) -> StopRow:
        return cls(
            id=row.id,
            host=row.host,
            email=row.email,
            note=row.note,
            created_by=row.created_by,
            created_at=row.created_at,
        )


class HeldRow(BaseModel):
    """Строка общего стоп-листа, которая держит продажи: снимают её не здесь."""

    id: int
    target: str
    reason: SuppressionReason
    stage: Stage | None
    created_at: datetime


class SalesStoplistView(BaseModel):
    rows: list[StopRow]
    total: int
    #: Общий стоп-лист, который держит продажи: новые первыми, не больше `GENERAL_SHOWN`.
    general: list[HeldRow]
    general_total: int
    #: Предел причины — экран проверяет им поле до нажатия.
    note_max: int


class StopRemark(BaseModel):
    """Строка списка, которая не легла: номер, почему словами, ячейка как есть."""

    line: int
    reason: str
    cell: str


class StoplistAdded(BaseModel):
    """Итог пополнения: сколько легло, сколько уже было, сколько лидов снято, что не легло."""

    added: int
    known: int
    closed: int
    remarks: list[StopRemark]


class StoplistRemoved(BaseModel):
    """Снятая запись — кому снова пишем — и сколько лидов вернулось на очистку."""

    id: int
    target: str
    released: int


@router.get("/stoplist", response_model=SalesStoplistView, summary="Стоп-лист продаж")
async def read_stoplist(
    user: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> SalesStoplistView:
    rows = await stoplist.rows(session)
    held, held_total = await _general(session, user)
    return SalesStoplistView(
        rows=[StopRow.of(row) for row in rows],
        total=len(rows),
        general=[HeldRow(**asdict(item)) for item in held],
        general_total=held_total,
        note_max=stoplist.NOTE_LENGTH,
    )


async def _general(session: AsyncSession, user: UserModel) -> tuple[list[suppressions.Held], int]:
    """Общий стоп-лист, который держит продажи, — с правом `view`, как на общем экране: отписки
    доноров и рекламодателей не видны учётке с одними «Продажами»."""
    if not has_permission(actor(user), Permission.VIEW):
        return [], 0
    return await suppressions.holding(session, limit=GENERAL_SHOWN)


async def _entries(file: UploadFile | None, text: str | None) -> stoplist.Entries:
    """Список с экрана — вставленный текстом или файлом, одно из двух. Разбор — в пуле
    потоков, как у загрузки базы: тысяча строк не держит цикл."""
    given = (text or "").strip()
    if file is not None and not given:
        data = await file.read(MAX_BYTES + 1)
        name = file.filename or "стоп-лист.csv"
        return await asyncio.to_thread(stoplist.read_upload, data, name)
    if given and file is None:
        return await asyncio.to_thread(stoplist.read_text, given)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, NOTHING_GIVEN)


@router.post("/stoplist", response_model=StoplistAdded, summary="Пополнить стоп-лист продаж")
async def add_to_stoplist(
    text: str | None = Form(None, description="адреса и домены — по строке"),
    file: UploadFile | None = File(None, description="CSV: в строке домен или адрес"),
    note: str | None = Form(None, description="причина словами: клиенты, партнёры"),
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> StoplistAdded:
    try:
        entries = await _entries(file, text)
        why = (note or "").strip() or (file.filename if file is not None else None)
        loaded = await stoplist.add(
            session, entries, author=author.email, note=why, author_id=author.id
        )
    except stoplist.StoplistError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    remarks = [StopRemark(line=line, reason=said, cell=cell) for line, said, cell in loaded.remarks]
    return StoplistAdded(
        added=loaded.added, known=loaded.known, closed=loaded.closed, remarks=remarks
    )


@router.post("/stoplist/{entry_id}/remove", response_model=StoplistRemoved, summary="Снять запись")
async def remove_from_stoplist(
    entry_id: int,
    author: UserModel = _seller,
    _: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> StoplistRemoved:
    try:
        removed = await stoplist.remove(session, entry_id, author_id=author.id)
    except stoplist.UnknownEntryError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await session.commit()
    return StoplistRemoved(id=entry_id, target=removed.target, released=removed.released)
