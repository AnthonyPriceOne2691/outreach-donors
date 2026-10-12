"""Рекламодатели списком и отчёты Этапа 2: список с фильтрами, выгрузка файлом,
приёмка по донору.

Сверка ТЗ 11.10.2026: рекламодателей было не увидеть списком, выгрузка файлом была
только у лидов, а приёмку ТЗ («по донору: 100 рекламодателей с контактами, письма
ушли 70%») было не посчитать нигде. Смотреть и выгружать — под правом `view`, как
список и выгрузку доноров: это то же содержимое, что очередь проверки и переписка.

Маршруты — своим модулем, а подключает их роутер рекламодателей (`routes.py`):
у экрана один префикс `/advertisers`, а файл маршрутов проверки не растёт отчётами.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.report_schemas import (
    AcceptanceView,
    AdvertiserCard,
    AdvertiserFacetsView,
    AdvertiserListView,
    AdvertiserPageQuery,
    AdvertiserQuery,
    DonorAcceptance,
    TallyView,
)
from backend.api.crawls.schemas import CrawlRow
from backend.api.deps import db_session, needs
from backend.config import crawl as crawl_cfg
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import acceptance, advertiser_list, board
from backend.features.crawl.advertiser_export import EXPORT_LIMIT, to_csv

router = APIRouter()

_viewer = Depends(needs(Permission.VIEW))


@router.get("/list", response_model=AdvertiserListView, summary="Рекламодатели списком")
async def listed(
    query: Annotated[AdvertiserPageQuery, Query()],
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AdvertiserListView:
    """Рекламодатели, которым пишем или напишем, — страницей, с фильтрами.

    Страница за концом — пустая, а не отказ: число страниц экран узнаёт по `total`.
    Счётчики фильтров — своим маршрутом (`/list/facets`).
    """
    found = await advertiser_list.page(
        session, query.filters(limit=query.limit, offset=(query.page - 1) * query.limit)
    )
    return AdvertiserListView(
        rows=[AdvertiserCard.of(row) for row in found.rows],
        total=found.total,
        page=query.page,
        limit=query.limit,
        export_limit=EXPORT_LIMIT,
    )


@router.get("/list/facets", response_model=AdvertiserFacetsView, summary="Счётчики фильтров списка")
async def listed_facets(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AdvertiserFacetsView:
    """Сколько рекламодателей с каждым значением фильтра — по всему списку, а не по
    фильтру и не по странице: они отвечают «что вообще есть». Отдельно от страницы —
    экран спрашивает их раз, а не на каждую смену страницы и фильтра."""
    return AdvertiserFacetsView.of(await advertiser_list.facets(session))


@router.get("/list/export", summary="Выгрузка рекламодателей файлом")
async def export(
    query: Annotated[AdvertiserQuery, Query()],
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Те же строки, что в списке под тем же фильтром, — файлом CSV.

    Выгружается то, что человек видит: фильтр — часть вопроса, на который он отвечает
    файлом. Сколько строк легло и сколько нашлось — заголовками ответа: экран говорит
    об этом словами, а не обещает больше, чем в файле.
    """
    found = await advertiser_list.page(session, query.filters(limit=EXPORT_LIMIT))
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(
        content=to_csv(found.rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="advertisers-{stamp}.csv"',
            "X-Export-Rows": str(len(found.rows)),
            "X-Export-Asked": str(found.total),
        },
    )


@router.get("/acceptance", response_model=AcceptanceView, summary="Приёмка по донорам")
async def accepted(
    limit: int = Query(default=200, ge=1, le=1000, description="доноров за раз"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AcceptanceView:
    """Каждый обойдённый донор: сколько рекламодателей найдено, сколько с адресом,
    какой доле ушло письмо — и все доноры вместе, по разным доменам."""
    report = await acceptance.acceptance(session, limit=limit)
    crawls = await board.latest_by_host(session, [row.host for row in report.donors])
    pages = crawl_cfg.MAX_PAGES_PER_DONOR
    return AcceptanceView(
        donors=[
            DonorAcceptance(
                host=row.host,
                crawl=CrawlRow.of(crawls[row.host], max_pages=pages)
                if row.host in crawls
                else None,
                tally=TallyView.of(row.tally),
                supplier=row.supplier,
            )
            for row in report.donors
        ],
        total=report.total,
        overall=TallyView.of(report.overall),
        target_with_address=acceptance.TARGET_WITH_ADDRESS,
        target_sent_share=acceptance.TARGET_SENT_SHARE,
    )
