"""Файлы к нашему ответу в переписке: приложить, убрать до отправки, скачать ушедший.

**Приложить и убрать — под правом ответа** (`send`): файл уходит наружу письмом,
и приложить его — половина отправки. Приложенный файл ждёт ответа строкой
переписки без письма; его номер идёт в `file_ids` ответа
(`POST /api/threads/{id}/answer`), и с этой минуты файл из письма не убирается.

**Скачать вложение нашего письма — под правом смотреть**, как вложение ответа
собеседника (`api/replies/routes.py`): это то же содержимое переписки. Отдаётся
оно только на скачивание (`replies/download.py`), никогда — на показ.

Правила файла — `letters/outgoing_files.py`, хранение — `letters/outgoing_store.py`;
отказ — словами (`api/errors.py`): что не так и какой предел.

**Файлы переписки и письма продаж — ещё и с правом «Продажи»** (решение Anthony
10.10.2026, П2): приложить, убрать и скачать без него — 403 словами
(`api/stage_access.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session
from backend.api.replies.download import download_headers
from backend.api.stage_access import on_message, on_thread
from backend.api.threads.schemas import OutgoingFileCard
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.letters.outgoing_files import ALLOWED, MAX_FILE_BYTES
from backend.features.letters.outgoing_store import OutgoingFiles

router = APIRouter(tags=["диалоги"])

#: Номер переписки или письма — из адреса: у продаж — ещё право «Продажи» (П2).
_thread_sender = Depends(on_thread(Permission.SEND))
_message_viewer = Depends(on_message(Permission.VIEW))


@router.post(
    "/threads/{thread_id}/files",
    response_model=OutgoingFileCard,
    summary="Приложить файл к ответу",
)
async def upload(
    thread_id: int,
    file: UploadFile = File(..., description=f"один файл: {ALLOWED}"),
    author: UserModel = _thread_sender,
    session: AsyncSession = Depends(db_session),
) -> OutgoingFileCard:
    """Файл к будущему ответу. Тип назначает сервер по белому списку, сверив
    расширение с содержимым; имя — очищенное. Отказ — 422 словами.

    Читается не больше предела и байтом сверх него: больший файл узнаётся
    по этому байту, а не по всему телу в памяти.
    """
    data = await file.read(MAX_FILE_BYTES + 1)
    row = await OutgoingFiles(session).keep(thread_id, file.filename, data, by=author.id)
    await session.commit()
    return OutgoingFileCard.of(row)


@router.delete(
    "/threads/{thread_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Убрать файл, пока он не ушёл",
)
async def remove(
    thread_id: int,
    file_id: int,
    _: UserModel = _thread_sender,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Убрать файл, который ни с одним письмом ещё не ушёл. Приложенный
    к письму — отказ 409 словами: письмо ушло или уйдёт вместе с ним."""
    await OutgoingFiles(session).remove(thread_id, file_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/messages/{message_id}/attachments/{file_id}",
    summary="Вложение нашего письма — файлом на скачивание",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "Файл"}},
)
async def attachment(
    message_id: int,
    file_id: int,
    _: UserModel = _message_viewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Файл, ушедший с нашим письмом, — только на скачивание, как вложение
    ответа собеседника: тип для браузера — двоичный, какой бы ни был у файла."""
    name, data = await OutgoingFiles(session).file(message_id, file_id)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers=download_headers(name, file_id),
    )
