"""База доноров: таблица с фильтрами и карточка.

Смотреть базу может каждый, у кого есть доступ: это то же содержимое,
что и переписка. Тратить деньги — другое право и другой экран.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.donors.schemas import DonorFullCard, DonorsPage
from backend.features.core.domain import DonorStatus, Permission
from backend.features.core.models.access import UserModel
from backend.features.donors.browse import DonorBrowser, DonorFilters
from backend.features.donors.export import to_csv

router = APIRouter(prefix="/donors", tags=["доноры"])

_viewer = Depends(needs(Permission.VIEW))

#: Потолок строк выгрузки. Экран отдаёт по сотне, файл читают не глазами;
#: но и без потолка нельзя — выгрузка всей базы одним ответом однажды
#: положит сервер ровно в тот момент, когда его попросят об отчёте.
EXPORT_LIMIT = 10_000


@router.get("", response_model=DonorsPage, summary="Таблица доноров")
async def all_donors(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
    status: DonorStatus | None = Query(default=None, description="вердикт по донору"),
    search: str | None = Query(default=None, description="по домену или причине отсева"),
    min_dr: int | None = Query(default=None, ge=0, le=100),
    has_contact: bool | None = Query(default=None, description="найден ли адрес"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DonorsPage:
    browser = DonorBrowser(session)
    page = await browser.page(
        DonorFilters(
            status=status,
            search=search,
            min_dr=min_dr,
            has_contact=has_contact,
            limit=limit,
            offset=offset,
        )
    )
    # Сводка считается по всей базе, а не по странице: она отвечает
    # на вопрос «что вообще есть», а не «что видно сейчас».
    return DonorsPage.of(page, await browser.counts_by_status())


@router.get("/export", summary="Выгрузка таблицы доноров")
async def export(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
    status: DonorStatus | None = Query(default=None, description="вердикт по донору"),
    search: str | None = Query(default=None, description="по домену или причине отсева"),
    min_dr: int | None = Query(default=None, ge=0, le=100),
    has_contact: bool | None = Query(default=None, description="найден ли адрес"),
) -> Response:
    """Те же строки, что на экране, файлом.

    **Выгружается то, что человек видит**, а не вся база: фильтр —
    часть вопроса, на который он отвечает выгрузкой. Выгрузка «всего»
    при включённом фильтре давала бы файл, не совпадающий с экраном,
    и разбираться в этом пришлось бы уже в чужой таблице.

    Потолок строк выше экранного: файл читают не глазами.
    """
    page = await DonorBrowser(session).page(
        DonorFilters(
            status=status,
            search=search,
            min_dr=min_dr,
            has_contact=has_contact,
            limit=EXPORT_LIMIT,
            offset=0,
        )
    )
    body = to_csv(page.rows)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="donors-{stamp}.csv"'},
    )


@router.get("/{donor_id}", response_model=DonorFullCard, summary="Карточка донора")
async def one_donor(
    donor_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DonorFullCard:
    return DonorFullCard.of(await DonorBrowser(session).card(donor_id))
