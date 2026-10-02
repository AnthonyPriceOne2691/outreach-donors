"""Загрузка базы лидов продаж: предпросмотр и загрузка — под правом `sales`.

Источник — файл (`file`) или ссылка на Google-таблицу (`link`), одно из двух,
формой `multipart/form-data`: сервер получает байты как есть, и общий читатель
отклоняет файл не в UTF-8 словами, а не кракозябрами. Сопоставление — JSON
`{"email": 0, "name": 2}` (поле → колонка с нуля); правка руками заменяет
угаданное целиком.

**Предпросмотр ничего не пишет**, загрузка пишет лидов и журнал одной транзакцией.
Состояния между шагами сервер не держит: мастер присылает источник оба раза,
таблица по ссылке читается заново.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import NonNegativeInt, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.schemas import IntakeView
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import intake, sheet
from backend.features.sales.columns import LeadField, Mapping

#: Лидов в ответе предпросмотра — показать, что получится. Отчёт — целиком.
PREVIEW_LEADS = 50

router = APIRouter(prefix="/sales", tags=["продажи"])

_seller = Depends(needs(Permission.SALES))
_MAPPING = TypeAdapter(dict[LeadField, NonNegativeInt])


async def sheet_http() -> AsyncIterator[httpx.AsyncClient]:
    """Клиент для Google-таблицы. Зависимостью — тест подменяет транспорт."""
    async with httpx.AsyncClient(timeout=sheet.TIMEOUT_S) as http:
        yield http


async def _found(
    file: UploadFile | None = File(None, description="CSV с базой"),
    link: str | None = Form(None, description="ссылка на Google-таблицу"),
    mapping: str | None = Form(None, description='JSON {"email": 0, …}: поле → колонка с нуля'),
    header: bool | None = Form(None, description="первая строка — заголовок; пусто — угадать"),
    delimiter: str | None = Form(None, description="разделитель, если угадан неверно"),
    http: httpx.AsyncClient = Depends(sheet_http),
) -> intake.Preview:
    """Источник → предпросмотр. Разбор — в пуле потоков: 5000 строк не держат цикл."""
    if file is not None and not link:
        data = await file.read(intake.MAX_BYTES + 1)
        name = file.filename or "база.csv"
        table = await asyncio.to_thread(intake.read_bytes, data, name, delimiter=delimiter)
    elif link and file is None:
        table = await intake.read_link(link, http, delimiter=delimiter)
    else:
        raise intake.IntakeError("дайте файл или ссылку на Google-таблицу — одно из двух")
    return await asyncio.to_thread(intake.preview, table, _mapping(mapping), header=header)


def _mapping(raw: str | None) -> Mapping | None:
    if not raw:
        return None
    try:
        return _MAPPING.validate_json(raw)
    except ValidationError as exc:
        raise intake.IntakeError(
            f"сопоставление не разобрано ({exc.error_count()} ош.): ждём JSON "
            '{"email": 0, "name": 1} — поле и номер колонки с нуля'
        ) from exc


@router.post("/import/preview", response_model=IntakeView, summary="Предпросмотр: без записи")
async def preview(_: UserModel = _seller, found: intake.Preview = Depends(_found)) -> IntakeView:
    return IntakeView.of(found, shown=PREVIEW_LEADS)


@router.post("/import", response_model=IntakeView, summary="Загрузить базу в гипотезу")
async def load(
    hypothesis_id: int = Form(..., description="гипотеза, в которую ложатся лиды"),
    author: UserModel = _seller,
    found: intake.Preview = Depends(_found),
    session: AsyncSession = Depends(db_session),
) -> IntakeView:
    loaded = await intake.load(session, found, hypothesis_id, author_id=author.id)
    await session.commit()
    return IntakeView.of(found, shown=PREVIEW_LEADS, loaded=loaded)
