"""Доноры с ценами списком: шаблон, предпросмотр, загрузка и очередь загрузки обхода.

Кнопка «Загрузить списком» рядом с «Завести донора вручную» на панели обхода (29.10.2026): до
десяти тысяч доноров за файл. Каждая строка — правилом формы (`donors/bulk_entry.py`), право —
то же, что у формы: `run`. Предпросмотр ничего не пишет, а запись проверяет всё заново: между
ними состояние доменов могло смениться.

**Обход загруженных — через очередь загрузки** (`crawl/backlog.py`), а не очередью обходов
сразу: десять тысяч обходов вперёд закрыли бы последние обходы и держали бы за собой обход,
запущенный человеком. Отчёт называет, сколько ждёт впереди и сколько это займёт при нынешних
обходчиках; очередь загрузки видна на панели и снимается целиком.

Разбор файла — в пуле потоков: десять тысяч строк XLSX не держат цикл событий, как у загрузки
базы продаж (`api/sales/routes._found`).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import backlog
from backend.features.donors.bulk_entry import Outcome, Report, Row, enter_many
from backend.features.donors.bulk_file import MAX_BYTES, Line, read_table, template_csv
from backend.shared.queue import CRAWL_QUEUE_NAME, workers_alive

#: Под маршрутами доноров (`routes.py` подключает его первым — до `/{donor_id}`): «/donors/import».
router = APIRouter(prefix="/import", tags=["доноры"])

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

#: Имя шаблона для браузера — латиницей запасное, кириллицей основное (`filename*`).
_TEMPLATE = "доноры-шаблон.csv"


class BulkRow(BaseModel):
    """Строка отчёта: номер строки файла, домен как написан, итог и почему."""

    line: int
    given: str
    outcome: Outcome
    host: str | None = None
    reason: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    crawlable: bool = False

    @classmethod
    def of(cls, row: Row) -> BulkRow:
        return cls(
            line=row.line,
            given=row.given,
            outcome=row.outcome,
            host=row.host,
            reason=row.reason,
            amount=row.amount,
            currency=row.currency,
            crawlable=row.crawlable,
        )


class BacklogView(BaseModel):
    """Очередь загрузки обхода: сколько ждёт, сколько это займёт, сколько обходчиков."""

    waiting: int
    #: Срок обойти всех ждущих, с, — по медиане последних обходов; `null` — обходчиков
    #: не знаем или их нет.
    eta_sec: float | None = None
    workers: int | None = None


class BulkReport(BaseModel):
    """Что вышло из файла — каждая строка и итоги."""

    file: str
    rows: list[BulkRow]
    created: int
    priced: int
    skipped: int
    #: Записаны и годны — обход их возьмёт (галочка «поставить в обход»).
    crawlable: int
    #: Поставлено в очередь загрузки этой загрузкой; у предпросмотра — 0.
    queued: int = 0
    #: Очередь загрузки после этой загрузки; у предпросмотра — какой она станет с галочкой.
    backlog: BacklogView

    @classmethod
    def of(cls, name: str, report: Report, *, queued: int, line: BacklogView) -> BulkReport:
        return cls(
            file=name,
            rows=[BulkRow.of(row) for row in report.rows],
            created=report.count(Outcome.CREATED),
            priced=report.count(Outcome.PRICED),
            skipped=report.count(Outcome.SKIPPED),
            crawlable=len(report.crawl_hosts),
            queued=queued,
            backlog=line,
        )


class Dropped(BaseModel):
    """Снятая очередь загрузки: сколько доноров в обход уже не пойдёт."""

    dropped: int


async def _workers() -> int | None:
    """Сколько обходчиков слушает очередь обходов — в пуле потоков, как у панели обхода."""
    return await asyncio.to_thread(workers_alive, queue=CRAWL_QUEUE_NAME)


async def _line(session: AsyncSession, more: int = 0) -> BacklogView:
    """Очередь загрузки сейчас — или какой она станет, если добавить `more`."""
    waiting, workers = await backlog.waiting(session) + more, await _workers()
    return BacklogView(
        waiting=waiting, eta_sec=await backlog.eta_sec(session, waiting, workers), workers=workers
    )


async def _read(file: UploadFile) -> list[Line]:
    """Строки файла; не прочитать целиком — 400 словами читателя (`BulkFileError` — отказ
    ввода руками, `ManualPriceError`): человек поправит файл."""
    data = await file.read(MAX_BYTES + 1)
    return await asyncio.to_thread(read_table, data, file.filename or "")


@router.get("/template", summary="Шаблон загрузки доноров списком")
async def template(_: UserModel = _viewer) -> Response:
    """CSV UTF-8 с заголовком и строкой-примером; метка BOM — чтобы Excel открыл кириллицу."""
    return Response(
        content="﻿" + template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=donors-template.csv; filename*=UTF-8''{quote(_TEMPLATE)}"
            )
        },
    )


@router.post("/preview", response_model=BulkReport, summary="Доноры списком: предпросмотр")
async def preview(
    file: UploadFile = File(..., description="CSV или XLSX: домен, цена, валюта, откуда цена"),
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> BulkReport:
    """Отчёт по строкам без записи — и какой станет очередь загрузки с галочкой «в обход»."""
    report = await enter_many(session, await _read(file), by=author.email, author_id=author.id)
    line = await _line(session, len(report.crawl_hosts))
    return BulkReport.of(file.filename or "", report, queued=0, line=line)


@router.post("", response_model=BulkReport, summary="Завести доноров списком")
async def enter_list(
    file: UploadFile = File(..., description="CSV или XLSX: домен, цена, валюта, откуда цена"),
    crawl: bool = Form(False, description="поставить записанных годных в обход"),
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> BulkReport:
    """Записать строки правилом формы; с `crawl` — годных в очередь загрузки обхода."""
    report = await enter_many(
        session, await _read(file), by=author.email, author_id=author.id, write=True
    )
    queued = (
        await backlog.add(session, report.crawl_hosts, by=author.email, author_id=author.id)
        if crawl
        else 0
    )
    await session.commit()
    return BulkReport.of(file.filename or "", report, queued=queued, line=await _line(session))


@router.get("/backlog", response_model=BacklogView, summary="Очередь загрузки обхода")
async def backlog_line(
    _: UserModel = _viewer, session: AsyncSession = Depends(db_session)
) -> BacklogView:
    return await _line(session)


@router.delete("/backlog", response_model=Dropped, summary="Снять очередь загрузки обхода")
async def drop_backlog(
    author: UserModel = _runner, session: AsyncSession = Depends(db_session)
) -> Dropped:
    """Загруженные доноры в обход больше не пойдут; уже идущие обходы идут. В журнал — кто."""
    dropped = await backlog.drop_all(session, author_id=author.id)
    await session.commit()
    return Dropped(dropped=dropped)
