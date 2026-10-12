"""Список рекламодателей и отчёты Этапа 2: что уходит на экран.

Своим файлом, а не в `schemas.py`: там очередь проверки, перевод и бизнесы ниши,
а здесь чтение — список, выгрузка и приёмка по донору (сверка ТЗ 11.10.2026).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.api.crawls.schemas import CrawlRow
from backend.features.core.domain import ContactStatus
from backend.features.crawl.acceptance import Tally
from backend.features.crawl.advertiser_list import (
    MAX_PAGE_SIZE,
    PAGE_SIZE,
    AddressState,
    AdvertiserFacets,
    AdvertiserFilters,
    AdvertiserRow,
)
from backend.features.crawl.offer_state import OfferState


class AdvertiserQuery(BaseModel):
    """Фильтры списка — одним набором у таблицы и у выгрузки.

    Имена те же, что в адресе экрана (`frontend/src/advertisers/listFilters.ts`):
    экран шлёт свою строку параметров как есть, и файл отвечает на тот же вопрос,
    что таблица.
    """

    search: str | None = Field(default=None, max_length=200, description="подстрока домена")
    donor: str | None = Field(
        default=None, max_length=255, description="донор, на котором нашли ссылку"
    )
    source: Literal["links", "niche"] | None = Field(
        default=None, description="по ссылке на доноре или бизнес ниши"
    )
    address: AddressState | None = Field(
        default=None, description="адрес в базе, только форма или ничего"
    )
    letter: OfferState | None = Field(default=None, description="что с оффером")

    def filters(self, *, limit: int, offset: int = 0) -> AdvertiserFilters:
        return AdvertiserFilters(
            search=self.search,
            donor=self.donor,
            source=self.source,
            address=self.address,
            letter=self.letter,
            limit=limit,
            offset=offset,
        )


class AdvertiserPageQuery(AdvertiserQuery):
    """Фильтры и страница — одной моделью: FastAPI разворачивает модель в параметры
    адреса, только когда других параметров у маршрута нет. Страница — номером,
    размер называет сервер."""

    page: int = Field(default=1, ge=1, le=1_000_000, description="страница, с единицы")
    limit: int = Field(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="строк на странице")


class AdvertiserCard(BaseModel):
    """Строка списка: кто, под какую ссылку, есть ли адрес и что с оффером."""

    id: int
    host: str
    source: str
    run_id: int | None
    donor_host: str | None
    page_url: str | None
    anchor: str | None
    points: int
    #: За что балл — причины скоринга словами: «rel=sponsored +5».
    reasons: list[str]
    #: Балл той же ссылки после пересчёта весов, если он не тот, с которым перевели.
    rescored: int | None
    donors: int
    contact_status: ContactStatus | None
    address: AddressState
    letter: OfferState

    @classmethod
    def of(cls, row: AdvertiserRow) -> AdvertiserCard:
        return cls(
            id=row.id,
            host=row.host,
            source=row.source,
            run_id=row.run_id,
            donor_host=row.donor_host,
            page_url=row.page_url,
            anchor=row.anchor,
            points=row.points,
            reasons=row.reasons,
            rescored=row.rescored,
            donors=row.donors,
            contact_status=row.contact_status,
            address=row.address,
            letter=row.letter,
        )


class AdvertiserFacetsView(BaseModel):
    """Счётчики под фильтрами — по всему списку, а не по фильтру."""

    sources: dict[str, int]
    addresses: dict[AddressState, int]
    letters: dict[OfferState, int]
    donors: dict[str, int]

    @classmethod
    def of(cls, facets: AdvertiserFacets) -> AdvertiserFacetsView:
        return cls(
            sources=facets.sources,
            addresses=facets.addresses,
            letters=facets.letters,
            donors=facets.donors,
        )


class AdvertiserListView(BaseModel):
    """Страница списка. `total` — строк под фильтром на всех страницах, `limit` —
    размер страницы: по ним экран считает страницы. `export_limit` — потолок строк
    выгрузки: кнопка не обещает больше, чем ляжет в файл. Счётчики фильтров — своим
    запросом (`/list/facets`): они про весь список и со страницей не меняются."""

    rows: list[AdvertiserCard]
    total: int
    page: int
    limit: int
    export_limit: int


class TallyView(BaseModel):
    """Числа приёмки (`crawl/acceptance.py`): найдено, переведено, с адресом, письмо
    ушло, доля ушедших от тех, что с адресом, и пройден ли порог ТЗ."""

    found: int
    promoted: int
    with_address: int
    sent: int
    sent_share: float | None
    accepted: bool

    @classmethod
    def of(cls, tally: Tally) -> TallyView:
        return cls(
            found=tally.found,
            promoted=tally.promoted,
            with_address=tally.with_address,
            sent=tally.sent,
            sent_share=tally.sent_share,
            accepted=tally.accepted,
        )


class DonorAcceptance(BaseModel):
    """Приёмка одного донора и его последний обход — тем же видом, что на панели
    «Обход доноров»: «обошли и не нашли никого» и «не обошли» читаются по-разному."""

    host: str
    crawl: CrawlRow | None
    tally: TallyView
    #: Донор-поставщик: его рекламодателей не трогаем, порог ему не задача.
    supplier: bool


class AcceptanceView(BaseModel):
    """Приёмка по донорам, все доноры вместе (по разным доменам, без поставщиков,
    справочно) и пороги ТЗ — от сервера, а не копией чисел в экране. `total` — сколько
    обойдённых доноров всего: строк бывает меньше (`limit`)."""

    donors: list[DonorAcceptance]
    total: int
    overall: TallyView
    target_with_address: int
    target_sent_share: float
