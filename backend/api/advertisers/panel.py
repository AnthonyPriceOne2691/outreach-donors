"""Панель раздела «Рекламодатели»: выборка и её шаги, качество загрузок, режим рассылки.

Только чтение, под правом `view`, как приёмка и список: это те же числа, собранные на один экран.
Маршруты — своим модулем, подключает их роутер рекламодателей (`routes.py`). План и факт рассылки
экран берёт из общего прогноза (`GET /letters/forecast?stage=advertisers`), части рекламодателей —
из счётчиков списка (`/advertisers/list/facets`): второго счёта тех же чисел здесь нет.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.panel_schemas import (
    PanelAdvertisers,
    PanelDonors,
    PanelLoad,
    PanelMailingView,
    PanelView,
    QualityHostRow,
    QualityHostsView,
    QualityView,
)
from backend.api.deps import db_session, needs
from backend.api.letters.autosend_schemas import QueueAutosendView
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.crawl import load_quality, loads, panel
from backend.features.crawl.niche import LINKS, NICHE
from backend.features.crawl.panel import Range
from backend.features.letters import autosend
from backend.shared.database.ids import INT4_MAX

router = APIRouter(prefix="/panel")

_viewer = Depends(needs(Permission.VIEW))

#: Сколько последних партий предлагать в выборе: старше — в таблице качества ниже.
LOADS_SHOWN = 30


@router.get("", response_model=PanelView, summary="Панель: от донора до дошедшего письма")
async def read_panel(
    chosen: Range = Query(default=Range.TODAY, alias="range", description="какая выборка"),
    load: int | None = Query(default=None, ge=1, le=INT4_MAX, description="номер партии"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> PanelView:
    """Шаги выборки. Партии нет — 404 словами: номер из адреса не показывает чужие числа."""
    try:
        window = await panel.window_of(session, chosen, load, now=datetime.now(UTC))
    except panel.UnknownLoadError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    found = await panel.funnel(session, window)
    recent = await loads.recent(session, limit=LOADS_SHOWN)
    return PanelView(
        range=chosen,
        since=window.since,
        load=next(
            (
                PanelLoad.of(row, hosts)
                for row, hosts in recent
                if window.load is not None and row.id == window.load.id
            ),
            None if window.load is None else PanelLoad.of(window.load, found.donors.added),
        ),
        loads=[PanelLoad.of(row, hosts) for row, hosts in recent],
        donors=PanelDonors.of(found.donors),
        advertisers=PanelAdvertisers.of(found),
    )


@router.get("/quality", response_model=QualityView, summary="Качество загрузок")
async def read_quality(
    page: int = Query(default=1, ge=1, le=INT4_MAX, description="страница"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> QualityView:
    """Загрузки, дни обходов без загрузки и ночной обход одной строкой — свежие первыми."""
    found = await load_quality.page(session, number=page)
    return QualityView.of(found, page=page, limit=load_quality.PAGE_SIZE)


@router.get("/quality/nights", response_model=QualityView, summary="Прошлые ночи ночного обхода")
async def read_quality_nights(
    page: int = Query(default=1, ge=1, le=INT4_MAX, description="страница"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> QualityView:
    """Ночи до последней, свежие первыми: последняя стоит строкой качества, эти — её раскрытием."""
    found = await load_quality.past_nights(session, number=page)
    return QualityView.of(found, page=page, limit=load_quality.NIGHTS_PAGE_SIZE)


@router.get("/quality/hosts", response_model=QualityHostsView, summary="Домены строки качества")
async def read_quality_hosts(
    load: int | None = Query(default=None, ge=1, le=INT4_MAX, description="номер партии"),
    day: date | None = Query(default=None, description="день обходов без загрузки"),
    night: date | None = Query(default=None, description="ночь ночного обхода"),
    page: int = Query(default=1, ge=1, le=INT4_MAX, description="страница"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> QualityHostsView:
    """Все домены партии, дня или ночи страницами: у строки качества их бывает десять тысяч."""
    if sum(named is not None for named in (load, day, night)) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Назовите партию, день или ночь — что-то одно"
        )
    found = await load_quality.hosts_of(session, load_id=load, day=day, night=night, number=page)
    return QualityHostsView(
        rows=[QualityHostRow.of(row) for row in found.rows],
        total=found.total,
        page=page,
        limit=load_quality.HOSTS_PAGE_SIZE,
    )


@router.get("/mailing", response_model=PanelMailingView, summary="Режим очереди рекламодателей")
async def read_mailing(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> PanelMailingView:
    """Уходит ли очередь сама — у обеих очередей этапа, тем же, что экран писем."""
    return PanelMailingView(
        links=QueueAutosendView.of(await autosend.state(session, Stage.ADVERTISERS, LINKS)),
        niche=QueueAutosendView.of(await autosend.state(session, Stage.ADVERTISERS, NICHE)),
    )
