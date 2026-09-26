"""Что отдают маршруты базы доноров."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models.donor import ContactModel
from backend.features.donors.browse import DonorCard as CardData
from backend.features.donors.browse import DonorFilters, DonorRow, Facets, Freshness
from backend.features.donors.browse import DonorPage as PageData
from backend.features.donors.standing import Waiting
from backend.features.donors.wording import reject_reason_text

#: Трафик «не ниже» больше этого — не порог, а опечатка: у самого большого
#: сайта базы разработки пять миллиардов, а колонка — восемь байт.
MAX_TRAFFIC = 10**15


class DonorQuery(BaseModel):
    """Фильтры таблицы доноров — одним набором у списка и у выгрузки.

    Имена те же, что в адресе экрана (`frontend/src/donors/donorFilters.ts`):
    экран шлёт свою строку параметров как есть, и выгрузка отвечает на тот
    же вопрос, что таблица.
    """

    status: DonorStatus | None = Field(default=None, description="вердикт по донору")
    search: str | None = Field(default=None, description="по домену или причине отсева")
    min_dr: int | None = Field(default=None, ge=0, le=100)
    has_contact: bool | None = Field(default=None, description="найден ли адрес")
    min_traffic: int | None = Field(
        default=None, ge=0, le=MAX_TRAFFIC, description="органический трафик не ниже"
    )
    geo: str | None = Field(
        default=None, pattern=r"^[A-Za-z]{2}$", description="страна донора, код ISO-2"
    )
    freshness: Freshness | None = Field(
        default=None, description="метрики: в сроке, пора обновить, не проверялись"
    )

    def filters(self, *, limit: int, offset: int = 0) -> DonorFilters:
        return DonorFilters(
            status=self.status,
            search=self.search,
            min_dr=self.min_dr,
            has_contact=self.has_contact,
            min_traffic=self.min_traffic,
            geo=self.geo.lower() if self.geo else None,
            freshness=self.freshness,
            limit=limit,
            offset=offset,
        )


class DonorPageQuery(DonorQuery):
    """Фильтры и страница. Страница — в той же модели: FastAPI разворачивает
    модель в параметры адреса, только когда других параметров у маршрута нет."""

    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PickedBody(BaseModel):
    """Отмеченные на экране доноры — номерами, телом запроса.

    Не в адресе: тысячи номеров упёрлись бы в предел строки запроса
    у прокси. Потолок числа номеров проверяет выгрузка — словами.
    """

    ids: list[int]


class DonorRowCard(BaseModel):
    """Строка таблицы доноров.

    `reject_reason` отдаётся всегда: «не подходит» без причины — это
    решение, которое нельзя оспорить, а пороги у нас версионируются
    именно затем, чтобы прошлые решения объяснялись. Отдаётся словами:
    код страны в ней — названием (`donors/wording.py`).
    """

    id: int
    host: str
    status: DonorStatus
    reject_reason: str | None
    dr: int | None
    org_traffic: int | None
    geo: str | None
    geo_top_share: float | None
    contacts: int
    contact_status: ContactStatus | None
    last_price: Decimal | None
    last_price_currency: str | None
    metrics_refreshed_at: datetime | None
    fresh: bool
    #: Метрики в сроке, пора обновить или не проверялись — тем же правилом,
    #: что у фильтра «Данные» (`browse.freshness`).
    freshness: Freshness

    @classmethod
    def of(cls, row: DonorRow) -> DonorRowCard:
        donor = row.donor
        return cls(
            id=donor.id,
            host=row.host,
            status=donor.status,
            reject_reason=reject_reason_text(donor.reject_reason),
            dr=donor.dr,
            org_traffic=donor.org_traffic,
            geo=donor.geo,
            geo_top_share=donor.geo_top_share,
            contacts=row.contacts,
            contact_status=donor.contact_status,
            last_price=donor.last_price,
            last_price_currency=donor.last_price_currency,
            metrics_refreshed_at=donor.metrics_refreshed_at,
            fresh=row.fresh,
            freshness=row.freshness,
        )


class WaitingCard(BaseModel):
    """Кто ждёт решения человека: из них получаются доноры."""

    domains: int
    #: Прогоны, в очередях которых ждут, новые первыми.
    runs: list[int]

    @classmethod
    def of(cls, waiting: Waiting) -> WaitingCard:
        return cls(domains=waiting.domains, runs=waiting.runs)


class DonorsPage(BaseModel):
    """Страница таблицы и общее число: без него фильтр не с чем сравнить,
    и «ничего не найдено» читается как «база пуста»."""

    rows: list[DonorRowCard]
    total: int
    #: Вердикт → сколько доноров, по всем донорам экрана, а не по фильтру.
    counts: dict[str, int]
    #: Страна → сколько доноров: варианты фильтра «Гео» со счётчиками.
    countries: dict[str, int]
    #: Состояние метрик → сколько доноров: варианты фильтра «Данные».
    freshness: dict[str, int]
    #: Сколько строк выгрузка кладёт в файл за раз. Подпись кнопки выгрузки
    #: не обещает больше: своей копии числа у экрана нет.
    export_limit: int
    #: Путь к первым донорам: пустой экран говорит, сколько ждёт решения.
    waiting: WaitingCard

    @classmethod
    def of(
        cls, page: PageData, facets: Facets, *, export_limit: int, waiting: Waiting
    ) -> DonorsPage:
        return cls(
            rows=[DonorRowCard.of(row) for row in page.rows],
            total=page.total,
            counts={status.value: count for status, count in facets.statuses.items()},
            countries=facets.countries,
            freshness={state.value: count for state, count in facets.freshness.items()},
            export_limit=export_limit,
            waiting=WaitingCard.of(waiting),
        )


class ContactCard(BaseModel):
    """Адрес донора и ступень, которая его дала."""

    id: int
    email: str
    source: ContactSource
    last_contacted_at: datetime | None
    last_replied_at: datetime | None
    #: Почему адрес нельзя удалить; пусто — можно. Отказ виден до нажатия.
    removal_refusal: str | None = None

    @classmethod
    def of(cls, contact: ContactModel, refusal: str | None = None) -> ContactCard:
        return cls(
            id=contact.id,
            email=contact.email,
            source=contact.source,
            last_contacted_at=contact.last_contacted_at,
            last_replied_at=contact.last_replied_at,
            removal_refusal=refusal,
        )


class DonorFullCard(BaseModel):
    """Карточка донора.

    Срок годности метрик отдаётся отдельным полем, а не выводится
    на фронте: правило «за свежее не платим второй раз» живёт в ядре,
    и второй его экземпляр в интерфейсе разъехался бы с первым.
    """

    id: int
    host: str
    status: DonorStatus
    reject_reason: str | None
    dr: int | None
    org_traffic: int | None
    geo: str | None
    geo_top_share: float | None
    geo_breakdown: list[dict[str, Any]] | None
    #: Разбивка неполная: спрашивали только верхнюю страну, её хватило
    #: для вердикта. Экран обязан это сказать, иначе одна строка выглядит
    #: как «у домена трафик из одной страны».
    geo_partial: bool
    metrics: dict[str, Any] | None
    metrics_refreshed_at: datetime | None
    expires_at: datetime | None
    fresh: bool
    freshness: Freshness
    contact_status: ContactStatus | None
    contact_attempted_at: datetime | None
    last_price: Decimal | None
    last_price_currency: str | None
    last_price_at: datetime | None
    #: Адреса в том порядке, в каком их берёт сборка писем: лучший первым.
    contacts: list[ContactCard]
    #: Почему поиск адреса сейчас не ставится; пусто — ставится. Правило
    #: то же, что у общего поиска, и решает его сервер: второй экземпляр
    #: на экране разошёлся бы с ним на первой правке.
    contact_refusal: str | None
    #: Решение человека: `accepted` — донор, `rejected` — отклонён, пусто —
    #: кандидат, ещё не решали (`donors/standing.py`).
    review: str | None
    #: Прогон, в очереди которого о домене решают или решили.
    review_run: int | None
    #: На какой адрес ушло бы первое письмо, если собрать очередь сейчас, —
    #: тем же запросом, что у сборки писем (`Recipients.letter_address`).
    letter_contact_id: int | None
    #: Почему письмо не соберётся ни на один адрес. Пусто вместе с адресом —
    #: адресов нет.
    letter_blocked: str | None

    @classmethod
    def of(cls, card: CardData) -> DonorFullCard:
        donor = card.donor
        return cls(
            id=donor.id,
            host=card.host,
            status=donor.status,
            reject_reason=reject_reason_text(donor.reject_reason),
            dr=donor.dr,
            org_traffic=donor.org_traffic,
            geo=donor.geo,
            geo_top_share=donor.geo_top_share,
            geo_breakdown=donor.geo_breakdown,
            geo_partial=donor.geo_partial,
            metrics=donor.metrics,
            metrics_refreshed_at=donor.metrics_refreshed_at,
            expires_at=card.expires_at,
            fresh=card.fresh,
            freshness=card.freshness,
            contact_status=donor.contact_status,
            contact_attempted_at=donor.contact_attempted_at,
            last_price=donor.last_price,
            last_price_currency=donor.last_price_currency,
            last_price_at=donor.last_price_at,
            contacts=[
                ContactCard.of(contact, card.removal.get(contact.id)) for contact in card.contacts
            ],
            contact_refusal=card.contact_refusal,
            review=donor.review,
            review_run=card.review_run,
            letter_contact_id=card.letter.contact_id,
            letter_blocked=card.letter.blocked,
        )
