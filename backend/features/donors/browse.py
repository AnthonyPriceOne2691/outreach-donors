"""Чтение базы доноров: таблица с фильтрами и карточка.

Отдельно от `repository.py`, который пишет: у чтения другие требования —
фильтры, страницы, соединения с контактами и переписки. Смешав их,
получаем модуль, в котором правка выборки задевает запись. Сами фильтры
и порядок — условиями в `table_filters.py`.

Одно правило отражено прямо в структуре: **«не проверен» и «не подходит» —
разные состояния.** Донор без данных Ahrefs не отсеян, его надо добрать
позже; поэтому фильтр по статусу их различает, а не сводит в «не годен».

**Таблица — доноры** (решение 26.09.2026, `standing.py`): строки, их число,
счётчики фильтров и выгрузка берут одну основу (`_screen`), и сузить экран
значит сузить её одну. Решение человека — выбор этой основы (`Review`, сверка
ТЗ 12.10.2026): без него — принятые, как было; «все проверенные» — таблица ТЗ.
Карточка открывается у любой строки `donors` — и честно говорит, донор это
или кандидат.

**Срок годности — одно условие** (`freshness`, `price_is`): им считается
и значок в строке, и фильтр. Два экземпляра правила «в сроке» разошлись бы
на самой границе срока, и фильтр «в сроке» показывал бы строку со значком
«пора обновить».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.manual import removal_refusals
from backend.features.contacts.preference import preferred_first
from backend.features.contacts.repository import search_refusal
from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.donors.price_freshness import PriceState, fresh_until, price_is
from backend.features.donors.price_refusal import price_refusal
from backend.features.donors.standing import UnknownDonorError, decided_in
from backend.features.donors.table_filters import (
    DonorFilters,
    DonorSort,
    Freshness,
    Review,
    decided,
    freshness,
    narrowed,
    ordered,
    top_country_counts,
)
from backend.features.letters.recipients import LetterAddress, Recipients
from backend.features.replies.extract import WITHOUT_PRICE
from backend.features.replies.quoting import written_by_hand
from backend.features.runs.thresholds import rules_in_force
from backend.features.topics.store import TOPIC_COLUMNS, topic_counts
from backend.shared.database.ids import storable

#: Сколько текста ответа показывает карточка рядом с ценой: прайс в письме укладывается,
#: а простыня пересланной переписки — нет; целиком письмо — в переписке по ссылке.
REPLY_TEXT_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class DonorRow:
    """Строка таблицы доноров."""

    donor: DonorModel
    host: str
    contacts: int
    freshness: Freshness
    #: Ответ донора на домене (`domains.seller_answer`): «не продаём» снимает цену,
    #: «бесплатно» ставит вместо неё отметку (`donors/price.py`).
    seller_answer: str | None
    #: Цена старше срока годности — тем же условием, что фильтр «цена устарела».
    price_stale: bool = False
    #: Тема сайта (`domains.site_topic*`): основная, вторая и почему.
    topic: str | None = None
    topic_secondary: str | None = None
    topic_reason: str | None = None

    @property
    def fresh(self) -> bool:
        """За свежие данные уже заплачено, и второй раз за них не платят."""
        return self.freshness is Freshness.FRESH

    @property
    def price_fresh_until(self) -> datetime | None:
        """До какого дня цена свежая — выгрузке: правилом `price_freshness`."""
        return fresh_until(self.donor)

    @property
    def price(self) -> Decimal | str | None:
        """Цена строки: последняя цена донора, а без неё — ответ, который её снял
        (`declines`, `free`). Словами его называет выгрузка (`export.WORDS`): пустая
        ячейка читалась бы «цену не спрашивали»."""
        if self.donor.last_price is not None:
            return self.donor.last_price
        return self.seller_answer if self.seller_answer in WITHOUT_PRICE else None


@dataclass(frozen=True, slots=True)
class DonorPage:
    """Страница таблицы и общее число — без него фильтр не с чем сравнить."""

    rows: list[DonorRow]
    total: int


@dataclass(frozen=True, slots=True)
class Facets:
    """Счётчики под фильтрами: сколько доноров с каждым значением.

    Считаются по всем донорам экрана — под выбранным решением человека, — а не
    по странице и не по остальным фильтрам: они отвечают на вопрос «что вообще есть»,
    и по ним пустой результат отличает «сузили до пустоты» от «такого нет вовсе».
    Решения (`reviews`) — по всей базе: это варианты самой основы экрана.
    """

    statuses: dict[DonorStatus, int] = field(default_factory=dict)
    countries: dict[str, int] = field(default_factory=dict)
    freshness: dict[Freshness, int] = field(default_factory=dict)
    prices: dict[PriceState, int] = field(default_factory=dict)
    reviews: dict[Review, int] = field(default_factory=dict)
    #: Тема → у скольких доноров экрана она основная или вторая; `none` — без темы.
    topics: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Picked:
    """Отмеченные строки для выгрузки и те, что в неё не попали.

    Между отметкой и выгрузкой донора могли перестать считать донором
    (решение человека сменилось) — такой в файл не идёт, и человек узнаёт,
    сколько их и почему, а не находит в файле меньше строк, чем отмечал.
    """

    rows: list[DonorRow]
    asked: int
    #: Запись есть, но под этим решением человека её уже нет.
    not_donors: int
    #: Записи нет вовсе.
    missing: int


@dataclass(frozen=True, slots=True)
class DonorCard:
    """Карточка донора: сам донор, его адреса и срок годности метрик."""

    donor: DonorModel
    host: str
    #: Ответ донора на домене — карточка показывает его вместо цены, которую он снял.
    seller_answer: str | None
    #: Адреса — в порядке, в каком их берёт сборка писем (`preferred_first`).
    contacts: Sequence[ContactModel]
    freshness: Freshness
    expires_at: datetime | None
    #: Почему поиск адреса сейчас не ставится; `None` — ставится. Считается
    #: сервером тем же правилом, что набирает общую очередь поиска: экран
    #: объясняет отказ до нажатия, а не узнаёт о нём после. Без умолчания:
    #: `None` здесь значит «можно искать», и забытое поле разрешало бы молча.
    contact_refusal: str | None
    #: Почему цену руками сейчас не записать; `None` — запишется. Слова те же,
    #: что у отказа «Записать цену» (`price_refusal.price_refusal`): экран
    #: говорит отказ до нажатия, а не узнаёт о нём после. Без умолчания —
    #: по той же причине, что у поиска.
    price_refusal: str | None
    #: Прогон, в очереди которого о домене решают или решили (`standing`).
    review_run: int | None
    #: На какой адрес ушло бы первое письмо — запросом сборки писем.
    letter: LetterAddress
    #: Какие адреса нельзя удалить: номер → почему (`contacts.manual`).
    removal: dict[int, str]
    #: Переписки с донором на Этапе 1 — новая первой: карточка ведёт в неё
    #: («Открыть диалог →», аудит экранов 09.10.2026). Диалог — на адрес, а
    #: не на домен, поэтому их бывает несколько.
    threads: Sequence[int]
    #: Цена старше срока годности — тем же условием, что фильтр таблицы.
    price_stale: bool = False
    #: Переписка ответа, из которого цена: карточка ведёт в неё. Пусто — цена руками,
    #: записана до 12.10.2026 или ответа уже нет.
    price_thread: int | None = None
    #: Что донор написал в этом ответе — без цитаты нашего письма и подписи, тем же
    #: правилом, что у разбора (`quoting.written_by_hand`). ТЗ: в карточке «распознанные
    #: цены … и рядом исходный текст ответа, из которого они взяты». Пусто — как выше.
    price_reply_text: str | None = None

    @property
    def fresh(self) -> bool:
        return self.freshness is Freshness.FRESH

    @property
    def price_expires_at(self) -> datetime | None:
        """До какого дня цена свежая: срок — от того, когда её назвали."""
        return fresh_until(self.donor)


def _expires_at(donor: DonorModel, ttl_days: int) -> datetime | None:
    if donor.metrics_refreshed_at is None:
        return None
    return donor.metrics_refreshed_at + timedelta(days=ttl_days)


def _screen(review: Review, *columns: Any) -> Select[Any]:
    """Основа экрана: донор с доменом — под решением человека (`table_filters.decided`).

    Строки, их число, счётчики фильтров и выгрузка берут её одну: счётчик
    «всей базы» рядом со списком доноров назвал бы донорами и кандидатов.
    """
    return (
        select(*columns)
        .select_from(DonorModel)
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(decided(review))
    )


class DonorBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _metrics_ttl(self) -> int:
        """Срок метрик — действующей версии настроек, как у прогона (`rules_in_force`)."""
        return (await rules_in_force(self._session)).metrics_ttl_days

    async def page(self, filters: DonorFilters, *, now: datetime | None = None) -> DonorPage:
        moment = now or datetime.now(UTC)
        ttl = await self._metrics_ttl()
        columns = (
            DonorModel,
            DomainModel.host,
            DomainModel.seller_answer,
            freshness(moment, ttl).label("freshness"),
            price_is(PriceState.STALE, moment).label("price_stale"),
            *TOPIC_COLUMNS,
        )
        rows = await self._session.execute(
            narrowed(_screen(filters.review, *columns), filters, moment, metrics_ttl_days=ttl)
            .order_by(*ordered(filters.sort))
            .limit(filters.limit)
            .offset(filters.offset)
        )
        found = rows.all()
        total = await self._session.scalar(
            narrowed(
                _screen(filters.review, func.count(DonorModel.id)),
                filters,
                moment,
                metrics_ttl_days=ttl,
            )
        )
        contacts = await self._contact_counts([donor.domain_id for donor, *_ in found])
        return DonorPage(
            rows=[
                DonorRow(
                    donor=donor,
                    host=host,
                    contacts=contacts.get(donor.domain_id, 0),
                    freshness=Freshness(state),
                    seller_answer=answer,
                    price_stale=bool(stale),
                    topic=topic,
                    topic_secondary=second,
                    topic_reason=why,
                )
                for donor, host, answer, state, stale, topic, second, why in found
            ],
            total=int(total or 0),
        )

    async def picked(
        self,
        ids: Sequence[int],
        *,
        limit: int,
        review: Review = Review.ACCEPTED,
        sort: DonorSort = DonorSort.DR,
        now: datetime | None = None,
    ) -> Picked:
        """Отмеченные доноры — независимо от фильтра на экране, но под тем же решением
        человека и в том же порядке: отмеченный на «все проверенные» и выгруженный там
        не «перестал быть донором».

        Номер, которого не может быть в базе (за пределом столбца), — такой
        же «записи нет», как любой другой несуществующий.
        """
        asked = list(dict.fromkeys(ids))
        known = tuple(one for one in asked if storable(one))
        filters = DonorFilters(ids=known, limit=limit, review=review, sort=sort)
        page = await self.page(filters, now=now) if known else None
        rows = page.rows if page is not None else []
        existing = (
            await self._session.scalar(
                select(func.count(DonorModel.id)).where(DonorModel.id.in_(known))
            )
            if known
            else 0
        )
        return Picked(
            rows=rows,
            asked=len(asked),
            not_donors=int(existing or 0) - len(rows),
            missing=len(asked) - int(existing or 0),
        )

    async def facets(self, review: Review = Review.ACCEPTED) -> Facets:
        """Счётчики под фильтрами — по всем донорам экрана под решением `review`."""
        moment = datetime.now(UTC)
        statuses = await self._session.execute(
            _screen(review, DonorModel.status, func.count(DonorModel.id)).group_by(
                DonorModel.status
            )
        )
        # Через подзапрос, а не `group_by(выражение)`: граница срока едет
        # параметром, и выражение в списке колонок и в группировке вышло бы
        # с разными номерами параметров — базе они разные.
        ttl = await self._metrics_ttl()
        states = _screen(review, freshness(moment, ttl).label("state")).subquery()
        by_state = await self._session.execute(
            select(states.c.state, func.count()).group_by(states.c.state)
        )
        return Facets(
            statuses=dict(statuses.tuples().all()),
            countries=await self._countries(review),
            freshness={Freshness(value): int(count) for value, count in by_state.tuples()},
            prices=await self._prices(review, moment),
            reviews=await self._reviews(),
            topics=await topic_counts(self._session, partial(_screen, review)),
        )

    async def _countries(self, review: Review) -> dict[str, int]:
        """Страна → у скольких доноров экрана она в топ-5 — тем же условием, что фильтр
        «Гео» (`table_filters.in_top_countries`): верхняя или в разбивке.

        Пункты фильтра — и страны разбивок (16.10.2026), а не только верхние: «Канада»,
        вторая у десяти доноров, иначе не появилась бы в списке вовсе, а счётчик пункта
        расходился бы с тем, что найдёт фильтр. Донор — один раз на страну: `UNION`
        сводит «верхняя и она же в разбивке». Разбивка — путём JSON в нестрогом режиме:
        пустая лежит JSON-значением null и даёт ноль стран, а не ошибку разбора массива.
        """
        rows = await self._session.execute(
            top_country_counts(_screen(review, DonorModel.id.label("donor")))
        )
        return {str(code).lower(): int(count) for code, count in rows.tuples()}

    async def _prices(self, review: Review, now: datetime) -> dict[PriceState, int]:
        """Сколько доноров экрана с ценой, со свежей, с устаревшей и без неё."""
        states = tuple(PriceState)
        row = (
            await self._session.execute(
                _screen(review, *(func.count().filter(price_is(state, now)) for state in states))
            )
        ).one()
        return {state: int(count) for state, count in zip(states, row, strict=True)}

    async def _reviews(self) -> dict[Review, int]:
        """Сколько записей под каждым решением человека — по всей базе."""
        states = tuple(Review)
        row = (
            await self._session.execute(
                select(*(func.count().filter(decided(state)) for state in states)).select_from(
                    DonorModel
                )
            )
        ).one()
        return {state: int(count) for state, count in zip(states, row, strict=True)}

    async def card(self, donor_id: int, *, sales_visible: bool = False) -> DonorCard:
        """Карточка донора. `sales_visible` — смотрящий видит продажи: письмо продаж в причине,
        почему письмо донору не уходит, — номером и ролью (`letters/other_role.py`)."""
        if not storable(donor_id):
            raise UnknownDonorError(f"Донора №{donor_id} нет")
        moment = datetime.now(UTC)
        ttl = await self._metrics_ttl()
        rows = await self._session.execute(
            select(
                DonorModel,
                DomainModel.host,
                DomainModel.seller_answer,
                freshness(moment, ttl).label("freshness"),
                price_is(PriceState.STALE, moment).label("price_stale"),
            )
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DonorModel.id == donor_id)
        )
        found = rows.first()
        if found is None:
            raise UnknownDonorError(f"Донора №{donor_id} нет")

        donor, host, answer, state, stale = found
        contacts = (
            (
                await self._session.execute(
                    select(ContactModel)
                    .where(ContactModel.domain_id == donor.domain_id)
                    .order_by(*preferred_first())
                )
            )
            .scalars()
            .all()
        )
        return DonorCard(
            donor=donor,
            host=host,
            seller_answer=answer,
            contacts=contacts,
            freshness=Freshness(state),
            expires_at=_expires_at(donor, ttl),
            contact_refusal=await search_refusal(self._session, donor),
            price_refusal=await price_refusal(self._session, host, donor),
            review_run=await decided_in(self._session, donor.domain_id, donor.review),
            letter=await Recipients(self._session).letter_address(
                donor.domain_id, sales_visible=sales_visible
            ),
            removal=await removal_refusals(self._session, contacts),
            threads=await self._threads(donor.domain_id),
            price_stale=bool(stale),
            **await self._price_reply(donor.last_price_reply_id),
        )

    async def _price_reply(self, reply_id: int | None) -> dict[str, Any]:
        """Ответ, из которого цена: его переписка и что в нём написал донор. Ответа
        нет — ни ссылки, ни текста."""
        if reply_id is None or not storable(reply_id):
            return {}
        found = (
            await self._session.execute(
                select(ReplyModel.thread_id, ReplyModel.raw_body).where(ReplyModel.id == reply_id)
            )
        ).first()
        if found is None:
            return {}
        thread, body = found
        return {
            "price_thread": thread,
            "price_reply_text": written_by_hand(body)[:REPLY_TEXT_LIMIT],
        }

    async def _threads(self, domain_id: int) -> list[int]:
        found = await self._session.scalars(
            select(ThreadModel.id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(ThreadModel.domain_id == domain_id, CampaignModel.stage == Stage.DONORS)
            .order_by(ThreadModel.id.desc())
        )
        return list(found)

    async def _contact_counts(self, domain_ids: Sequence[int]) -> dict[int, int]:
        if not domain_ids:
            return {}
        rows = await self._session.execute(
            select(ContactModel.domain_id, func.count(ContactModel.id))
            .where(ContactModel.domain_id.in_(domain_ids))
            .group_by(ContactModel.domain_id)
        )
        return dict(rows.tuples().all())
