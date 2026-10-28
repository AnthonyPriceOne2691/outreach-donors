"""Что отдают маршруты базы доноров."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from backend.api.contacts.reach import ReachCard, ReachRow, reach_fields
from backend.api.threads.schemas import OfferCard
from backend.api.topics.schemas import TopicCount, TopicFields, topic_counts, topic_fields
from backend.config import filters as filters_cfg
from backend.features.contacts.preference import DEAD
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus, PriceSource
from backend.features.core.models.donor import ContactModel
from backend.features.donors.browse import DonorCard as CardData
from backend.features.donors.browse import DonorPage as PageData
from backend.features.donors.browse import DonorRow, Facets
from backend.features.donors.geo import stored_shares
from backend.features.donors.manual_price import (
    DEFAULT_CURRENCY,
    Entered,
    ManualPrice,
    manual_price,
)
from backend.features.donors.price_freshness import PriceState
from backend.features.donors.standing import Waiting
from backend.features.donors.table_filters import DonorFilters, DonorSort, Freshness, Review
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
    has_contact: bool | None = Field(default=None, description="есть ли у донора адрес в базе")
    min_traffic: int | None = Field(
        default=None, ge=0, le=MAX_TRAFFIC, description="органический трафик не ниже"
    )
    geo: str | None = Field(
        default=None, pattern=r"^[A-Za-z]{2}$", description="страна в топ-5 донора, код ISO-2"
    )
    freshness: Freshness | None = Field(
        default=None, description="метрики: в сроке, пора обновить, не проверялись"
    )
    price: PriceState | None = Field(
        default=None, description="цена: есть, свежая, устарела (старше срока) или нет"
    )
    review: Review = Field(
        default=Review.ACCEPTED,
        description="решение человека: принятые (доноры), отклонённые, без решения или все",
    )
    sort: DonorSort = Field(default=DonorSort.DR, description="порядок строк")
    topic: str | None = Field(
        default=None, pattern=r"^[a-z_]{2,32}$", description="тематика: код темы или none"
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
            price=self.price,
            topic=self.topic,
            review=self.review,
            sort=self.sort,
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
    #: Решение человека экрана, на котором отмечали: отмеченный на «все проверенные»
    #: выгружается и там, а не считается «переставшим быть донором».
    review: Review = Review.ACCEPTED
    #: Порядок экрана: файл отмеченных — в том же порядке, что таблица (`DonorsScope`).
    sort: DonorSort = DonorSort.DR


class CountryShareCard(BaseModel):
    """Страна разбивки донора и её доля органического трафика (0…1)."""

    country: str
    share: float


class DonorRowCard(TopicFields, ReachRow):
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
    #: Топ-5 стран с долями, по убыванию трафика (16.10.2026): колонка «Гео» показывает
    #: под верхней остальные. Пусто — стран не спрашивали.
    geo_breakdown: list[CountryShareCard] | None = None
    #: Разбивка неполная — как у карточки (`DonorFullCard.geo_partial`): экран говорит,
    #: что остальные страны не запрашивались, а не выдаёт одну за весь трафик.
    geo_partial: bool = False
    contacts: int
    contact_status: ContactStatus | None
    last_price: Decimal | None
    last_price_currency: str | None
    #: Когда цену назвали — от этого дня считается её срок годности.
    last_price_at: datetime | None = None
    #: Цена старше срока годности — тем же условием, что фильтр «цена устарела»
    #: (`table_filters.price_is`): сначала перезапрос, потом Этап 2.
    price_stale: bool = False
    #: Ответ донора, снявший цену: «не продаёт» и «бесплатно» стоят на её месте.
    seller_answer: str | None = None
    #: Решение человека: `accepted`, `rejected` или пусто — не решали. Видно тогда,
    #: когда экран показывает не только доноров (фильтр «Решение»).
    review: str | None = None
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
            geo_breakdown=[
                CountryShareCard(country=country, share=share)
                for country, share in stored_shares(donor.geo_breakdown)
            ]
            or None,
            geo_partial=donor.geo_partial,
            contacts=row.contacts,
            contact_status=donor.contact_status,
            last_price=donor.last_price,
            last_price_currency=donor.last_price_currency,
            last_price_at=donor.last_price_at,
            price_stale=row.price_stale,
            seller_answer=row.seller_answer,
            review=donor.review,
            metrics_refreshed_at=donor.metrics_refreshed_at,
            fresh=row.fresh,
            freshness=row.freshness,
            **topic_fields(row.topic, row.topic_secondary, row.topic_reason),
            deeper=row.deeper,
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
    #: Страна → у скольких доноров она в топ-5: варианты фильтра «Гео» со счётчиками,
    #: тем же условием, что фильтр (`table_filters.in_top_countries`).
    countries: dict[str, int]
    #: Состояние метрик → сколько доноров: варианты фильтра «Данные».
    freshness: dict[str, int]
    #: Состояние цены → сколько доноров: варианты фильтра «Цена».
    prices: dict[str, int] = Field(default_factory=dict)
    #: Решение человека → сколько записей во всей базе: варианты фильтра «Решение».
    reviews: dict[str, int] = Field(default_factory=dict)
    #: Срок годности цены, дней: экран пишет «старше N дней» числом сервера.
    price_ttl_days: int = filters_cfg.PRICE_TTL_DAYS
    #: Сколько строк выгрузка кладёт в файл за раз. Подпись кнопки выгрузки
    #: не обещает больше: своей копии числа у экрана нет.
    export_limit: int
    #: Путь к первым донорам: пустой экран говорит, сколько ждёт решения.
    waiting: WaitingCard
    #: Пункты фильтра «Тематика» со счётчиками — тем же условием, что фильтр.
    topics: list[TopicCount] = Field(default_factory=list)

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
            prices={state.value: count for state, count in facets.prices.items()},
            reviews={review.value: count for review, count in facets.reviews.items()},
            topics=topic_counts(facets.topics),
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
    #: Письмо сюда не дошло: ящика нет или его больше не читают
    #: (`preference.DEAD`). Письма на такой адрес не собираются.
    bounced: bool = False

    @classmethod
    def of(cls, contact: ContactModel, refusal: str | None = None) -> ContactCard:
        return cls(
            id=contact.id,
            email=contact.email,
            source=contact.source,
            last_contacted_at=contact.last_contacted_at,
            last_replied_at=contact.last_replied_at,
            removal_refusal=refusal,
            bounced=contact.verification_status == DEAD,
        )


class DonorFullCard(ReachCard):
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
    #: Разбивка неполная: в ней одна верхняя страна, остальные не запрашивались
    #: (`donors.geo_partial`). Экран обязан это сказать, иначе одна строка выглядит
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
    #: Все цены того же ответа, что последняя цена, словами донора. Пусто —
    #: цена записана до 06.10.2026, когда списка ещё не было, или указана руками.
    last_offers: list[OfferCard] | None = None
    #: Откуда последняя цена: `reply` — из ответа донора, `manual` — указал
    #: человек. Пусто — записана до 07.10.2026, то есть из ответа.
    last_price_source: PriceSource | None = None
    #: У ручной цены — заметка «откуда цена» и кто её указал.
    last_price_note: str | None = None
    last_price_by: str | None = None
    #: Ответ донора на домене: `sells`, `declines`, `free`. Последнее слово донора
    #: побеждает (11.10.2026): «не продаём» снимает цену, «бесплатно» ставит вместо
    #: неё отметку — карточка показывает ответ там, где без него была бы пустая цена.
    seller_answer: str | None = None
    #: Что донор сказал рядом с ценой (сверка ТЗ 12.10.2026): белая и серая раздельно
    #: (валюта у обеих — `last_price_currency`), оплата, срок публикации в днях,
    #: `dofollow` / `nofollow`. Пусто — не назвал или цена записана раньше.
    last_price_white: Decimal | None = None
    last_price_grey: Decimal | None = None
    last_payment_methods: list[str] | None = None
    last_placement_days: int | None = None
    last_link_type: str | None = None
    #: Ответ, из которого цена, и его переписка — ссылка из карточки. Пусто — цена
    #: руками, записана раньше или ответа больше нет.
    last_price_reply_id: int | None = None
    last_price_thread_id: int | None = None
    #: Что донор написал в этом ответе (без цитаты и подписи) — рядом с ценой, как просит
    #: ТЗ: «распознанные цены … и рядом исходный текст ответа». Пусто — ответа нет.
    last_price_reply_text: str | None = None
    #: Цена старше срока годности и до какого дня она свежая — правилом сервера.
    price_stale: bool = False
    price_expires_at: datetime | None = None
    #: Адреса в том порядке, в каком их берёт сборка писем: лучший первым.
    contacts: list[ContactCard]
    #: Почему поиск адреса сейчас не ставится; пусто — ставится. Правило
    #: то же, что у общего поиска, и решает его сервер: второй экземпляр
    #: на экране разошёлся бы с ним на первой правке.
    contact_refusal: str | None
    #: Почему цену руками сейчас не записать; пусто — запишется. Слова те же,
    #: что у отказа «Записать цену» (`donors/manual_price.price_refusal`).
    price_refusal: str | None
    #: Решение человека: `accepted` — донор, `rejected` — отклонён, пусто —
    #: кандидат, ещё не решали (`donors/standing.py`).
    review: str | None
    #: Кто и когда решил — в карточке рядом с решением (боевой прогон 06.10:
    #: «принят» было видно только в списке, а кем и когда — нигде).
    review_by: str | None = None
    review_at: datetime | None = None
    #: Донор заведён вручную, с ценой, а не принят в очереди прогона, — кем.
    entered_by: str | None = None
    #: Прогон, в очереди которого о домене решают или решили.
    review_run: int | None
    #: На какой адрес ушло бы первое письмо, если собрать очередь сейчас, —
    #: тем же запросом, что у сборки писем (`Recipients.letter_address`).
    letter_contact_id: int | None
    #: Почему письмо не соберётся ни на один адрес. Пусто вместе с адресом —
    #: адресов нет.
    letter_blocked: str | None
    #: Что сказать рядом с адресом письма: прежние письма не дошли, и это
    #: уйдёт на следующий адрес. Пусто — сказать нечего.
    letter_note: str | None = None
    #: Переписки с донором на Этапе 1 — новая первой: из карточки — «Открыть
    #: диалог →» в неё. Пусто — писем донору ещё не было.
    threads: list[int] = Field(default_factory=list)

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
            last_offers=OfferCard.listed(donor.last_offers),
            last_price_source=donor.last_price_source,
            last_price_note=donor.last_price_note,
            last_price_by=donor.last_price_by,
            seller_answer=card.seller_answer,
            last_price_white=donor.last_price_white,
            last_price_grey=donor.last_price_grey,
            last_payment_methods=donor.last_payment_methods,
            last_placement_days=donor.last_placement_days,
            last_link_type=donor.last_link_type,
            last_price_reply_id=donor.last_price_reply_id,
            last_price_thread_id=card.price_thread,
            last_price_reply_text=card.price_reply_text,
            price_stale=card.price_stale,
            price_expires_at=card.price_expires_at,
            contacts=[
                ContactCard.of(contact, card.removal.get(contact.id)) for contact in card.contacts
            ],
            contact_refusal=card.contact_refusal,
            price_refusal=card.price_refusal,
            review=donor.review,
            review_by=donor.review_by,
            review_at=donor.review_at,
            entered_by=donor.entered_by,
            review_run=card.review_run,
            letter_contact_id=card.letter.contact_id,
            letter_blocked=card.letter.blocked,
            letter_note=card.letter.note,
            threads=list(card.threads),
            **reach_fields(
                donor, form_url=card.form_url, refusal=card.deeper_refusal, in_forms=card.in_forms
            ),
        )


class PriceBody(BaseModel):
    """Цена, которую человек знает сам. Проверяет ядро — словами
    (`donors/manual_price.py`): тот же текст отказа у кнопки и у консоли."""

    #: Число или строка: строку («150.50») ядро разберёт и, если это не цена,
    #: скажет словами, а не отказом разбора схемы.
    price: Decimal | str
    currency: str = Field(default=DEFAULT_CURRENCY, description="код или знак валюты")
    note: str | None = Field(
        default=None, description="откуда цена: «прайс агентства», «LinkDetective»"
    )
    #: Серая цена (без пометки «реклама») и способы оплаты — если человек их знает
    #: (сверка ТЗ 12.10.2026). Проверяет ядро теми же правилами, что цену.
    price_grey: Decimal | str | None = Field(default=None, description="серая цена, та же валюта")
    payment_methods: list[str] | str | None = Field(
        default=None, description="способы оплаты списком или строкой через запятую"
    )

    def checked(self, *, by: str) -> ManualPrice:
        """Вписанное — проверенной ценой руками или отказом словами ядра."""
        return manual_price(
            self.price,
            self.currency,
            self.note,
            by=by,
            grey=self.price_grey,
            payment=self.payment_methods,
        )


class ManualDonorBody(PriceBody):
    """Донор, заведённый вручную: домен и цена."""

    host: str = Field(description="домен или адрес сайта: приводится к корню, как у прогона")


class EnteredDonor(BaseModel):
    """Что вышло из «Завести донора вручную»."""

    donor_id: int
    host: str
    #: Донором домен стал сейчас; `false` — уже был донором, записана цена.
    created: bool
    #: Годен по порогам — обход Этапа 2 его берёт.
    suitable: bool
    price: Decimal
    currency: str

    @classmethod
    def of(cls, entered: Entered, price: ManualPrice) -> EnteredDonor:
        return cls(
            donor_id=entered.donor_id,
            host=entered.host,
            created=entered.created,
            suitable=entered.suitable,
            price=price.amount,
            currency=price.currency,
        )
