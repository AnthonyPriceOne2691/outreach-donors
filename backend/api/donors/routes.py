"""База доноров: таблица с фильтрами, выгрузка и карточка.

Смотреть базу может каждый, у кого есть доступ: это то же содержимое,
что и переписка. Тратить деньги — другое право и другой экран.

**Список — только доноры, принятые человеком** (`donors/standing.py`,
решение 26.09.2026). Карточка открывается у любой записи `donors`
и сама говорит, донор это или кандидат.

**Цена руками** (07.10.2026) — «Указать цену» на карточке и «Завести донора
вручную» на панели обхода: Этап 2 запускается только по донорам с известной
ценой, а агентство знает цены многих сайтов само. Правило одно с консолью
(`donors/manual_price.py`), право то же, что у адреса руками, — `run`, в
журнале — кто, сколько и откуда цена.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.donors.schemas import (
    DonorFullCard,
    DonorPageQuery,
    DonorQuery,
    DonorsPage,
    EnteredDonor,
    ManualDonorBody,
    PickedBody,
    PriceBody,
)
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.donors.browse import DonorBrowser, DonorRow
from backend.features.donors.export import EXPORT_LIMIT, checked_picks, to_csv
from backend.features.donors.manual_price import enter_host, manual_price, price_donor
from backend.features.donors.standing import waiting

router = APIRouter(prefix="/donors", tags=["доноры"])

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))


@router.get("", response_model=DonorsPage, summary="Таблица доноров")
async def all_donors(
    query: Annotated[DonorPageQuery, Query()],
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DonorsPage:
    browser = DonorBrowser(session)
    page = await browser.page(query.filters(limit=query.limit, offset=query.offset))
    # Счётчики фильтров — по всем донорам экрана, а не по странице: они
    # отвечают на вопрос «что вообще есть», а не «что видно сейчас».
    return DonorsPage.of(
        page,
        await browser.facets(),
        export_limit=EXPORT_LIMIT,
        waiting=await waiting(session),
    )


@router.get("/export", summary="Выгрузка найденных доноров")
async def export(
    query: Annotated[DonorQuery, Query()],
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Те же строки, что на экране, файлом.

    **Выгружается то, что человек видит**, а не вся база: фильтр —
    часть вопроса, на который он отвечает выгрузкой. Выгрузка «всего»
    при включённом фильтре давала бы файл, не совпадающий с экраном,
    и разбираться в этом пришлось бы уже в чужой таблице.

    Потолок строк выше экранного: файл читают не глазами. Сколько строк
    в файле и сколько нашлось всего — заголовками ответа: экран говорит
    об этом словами, а не обещает больше, чем в файле.
    """
    page = await DonorBrowser(session).page(query.filters(limit=EXPORT_LIMIT))
    return _file(page.rows, {"X-Export-Asked": page.total})


@router.post("/export", summary="Выгрузка отмеченных доноров")
async def export_picked(
    body: PickedBody,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Отмеченные на экране доноры файлом — независимо от фильтра.

    Номера — телом запроса: тысячи номеров в адресе упёрлись бы в предел
    строки запроса у прокси. Номер, переставший быть донором между отметкой
    и выгрузкой, в файл не идёт, а заголовки ответа говорят, сколько таких.
    """
    picked = await DonorBrowser(session).picked(checked_picks(body.ids), limit=EXPORT_LIMIT)
    return _file(
        picked.rows,
        {
            "X-Export-Asked": picked.asked,
            "X-Export-Not-Donors": picked.not_donors,
            "X-Export-Missing": picked.missing,
        },
    )


def _file(rows: list[DonorRow], counts: dict[str, int]) -> Response:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(
        content=to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="donors-{stamp}.csv"',
            "X-Export-Rows": str(len(rows)),
            **{name: str(value) for name, value in counts.items()},
        },
    )


@router.get("/{donor_id}", response_model=DonorFullCard, summary="Карточка донора")
async def one_donor(
    donor_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DonorFullCard:
    return DonorFullCard.of(await DonorBrowser(session).card(donor_id))


@router.post("", response_model=EnteredDonor, summary="Завести донора вручную, с ценой")
async def enter_donor(
    body: ManualDonorBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> EnteredDonor:
    """Домен и цена, которую агентство знает само, — донором Этапа 2.

    Домен не донор — становится им, принятым человеком без прогона; уже
    донор — ему записывается цена. Ahrefs и другие платные сервисы не
    зовутся. Отказ — словами ядра, до записи.
    """
    price = manual_price(body.price, body.currency, body.note, by=author.email)
    entered = await enter_host(session, body.host, price, author_id=author.id)
    await session.commit()
    return EnteredDonor.of(entered, price)


@router.post("/{donor_id}/price", response_model=DonorFullCard, summary="Указать цену донору")
async def set_price(
    donor_id: int,
    body: PriceBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> DonorFullCard:
    """Цена, которую человек знает сам, — последней ценой донора.

    Ответ — карточка целиком: цена и её источник показываются словами
    сервера, а не догадкой экрана.
    """
    price = manual_price(body.price, body.currency, body.note, by=author.email)
    await price_donor(session, donor_id, price, author_id=author.id)
    await session.commit()
    return DonorFullCard.of(await DonorBrowser(session).card(donor_id))
