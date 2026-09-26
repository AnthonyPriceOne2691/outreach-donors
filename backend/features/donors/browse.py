"""Чтение базы доноров: таблица с фильтрами и карточка.

Отдельно от `repository.py`, который пишет: у чтения другие требования —
фильтры, страницы, соединения с контактами и переписки. Смешав их,
получаем модуль, в котором правка выборки задевает запись.

Одно правило отражено прямо в структуре: **«не проверен» и «не подходит» —
разные состояния.** Донор без данных Ahrefs не отсеян, его надо добрать
позже; поэтому фильтр по статусу их различает, а не сводит в «не годен».

**Таблица — только доноры** (решение 26.09.2026, `standing.py`): строки,
их число, счётчики фильтров и выгрузка берут одну основу (`_screen`),
и сузить экран значит сузить её одну. Карточка открывается у любой строки
`donors` — и честно говорит, донор это или кандидат.

**Срок годности метрик — одно условие** (`freshness`): им считается
и значок в строке, и фильтр «Данные». Два экземпляра правила «в сроке»
разошлись бы на самой границе срока, и фильтр «в сроке» показывал бы строку
со значком «пора обновить».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.contacts.manual import removal_refusals
from backend.features.contacts.preference import preferred_first
from backend.features.contacts.repository import search_refusal
from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.donors.standing import decided_in, is_donor
from backend.features.letters.recipients import LetterAddress, Recipients
from backend.shared.database.ids import storable


class UnknownDonorError(ValueError):
    """Донора с таким номером нет."""


class Freshness(StrEnum):
    """Метрики донора: в сроке, пора обновить, не проверялись.

    «Не проверялись» — не «пора обновить»: у второго данные были и устарели,
    у первого их не было вовсе, и обновлять нечего.
    """

    FRESH = "fresh"
    STALE = "stale"
    NEVER = "never"


def _freshness_is(state: Freshness, now: datetime) -> ColumnElement[bool]:
    """Условие одного состояния. Три условия не пересекаются и покрывают всё.

    Граница — та же, что у прогона, решающего, платить ли за метрики снова
    (`DonorRepository.fresh_hosts`): строго позже, чем срок назад.
    """
    refreshed = DonorModel.metrics_refreshed_at
    border = now - timedelta(days=filters_cfg.METRICS_TTL_DAYS)
    if state is Freshness.NEVER:
        return refreshed.is_(None)
    if state is Freshness.FRESH:
        return refreshed > border
    return refreshed <= border


def freshness(now: datetime) -> ColumnElement[str]:
    """Состояние метрик строкой — теми же условиями, что у фильтра."""
    return case(
        *((_freshness_is(state, now), state.value) for state in Freshness),
    )


@dataclass(frozen=True, slots=True)
class DonorFilters:
    """Чем сужают таблицу. Пустое поле — «не сужать»."""

    status: DonorStatus | None = None
    search: str | None = None
    min_dr: int | None = None
    has_contact: bool | None = None
    #: Органический трафик не ниже. Бывает до миллиардов — колонка `BigInteger`.
    min_traffic: int | None = None
    #: Страна донора, код ISO-2 (`donors.geo`).
    geo: str | None = None
    freshness: Freshness | None = None
    #: Только эти номера — выгрузка отмеченных. Пусто — не сужать.
    ids: tuple[int, ...] = ()
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class DonorRow:
    """Строка таблицы доноров."""

    donor: DonorModel
    host: str
    contacts: int
    freshness: Freshness

    @property
    def fresh(self) -> bool:
        """За свежие данные уже заплачено, и второй раз за них не платят."""
        return self.freshness is Freshness.FRESH


@dataclass(frozen=True, slots=True)
class DonorPage:
    """Страница таблицы и общее число — без него фильтр не с чем сравнить."""

    rows: list[DonorRow]
    total: int


@dataclass(frozen=True, slots=True)
class Facets:
    """Счётчики под фильтрами: сколько доноров с каждым значением.

    Считаются по всем донорам экрана, а не по странице и не по остальным
    фильтрам: они отвечают на вопрос «что вообще есть», и по ним пустой
    результат отличает «сузили до пустоты» от «такого нет вовсе».
    """

    statuses: dict[DonorStatus, int] = field(default_factory=dict)
    countries: dict[str, int] = field(default_factory=dict)
    freshness: dict[Freshness, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Picked:
    """Отмеченные строки для выгрузки и те, что в неё не попали.

    Между отметкой и выгрузкой донора могли перестать считать донором
    (решение человека сменилось) — такой в файл не идёт, и человек узнаёт,
    сколько их и почему, а не находит в файле меньше строк, чем отмечал.
    """

    rows: list[DonorRow]
    asked: int
    #: Запись есть, но это уже не донор.
    not_donors: int
    #: Записи нет вовсе.
    missing: int


@dataclass(frozen=True, slots=True)
class DonorCard:
    """Карточка донора: сам донор, его адреса и срок годности метрик."""

    donor: DonorModel
    host: str
    #: Адреса — в порядке, в каком их берёт сборка писем (`preferred_first`).
    contacts: Sequence[ContactModel]
    freshness: Freshness
    expires_at: datetime | None
    #: Почему поиск адреса сейчас не ставится; `None` — ставится. Считается
    #: сервером тем же правилом, что набирает общую очередь поиска: экран
    #: объясняет отказ до нажатия, а не узнаёт о нём после. Без умолчания:
    #: `None` здесь значит «можно искать», и забытое поле разрешало бы молча.
    contact_refusal: str | None
    #: Прогон, в очереди которого о домене решают или решили (`standing`).
    review_run: int | None
    #: На какой адрес ушло бы первое письмо — запросом сборки писем.
    letter: LetterAddress
    #: Какие адреса нельзя удалить: номер → почему (`contacts.manual`).
    removal: dict[int, str]

    @property
    def fresh(self) -> bool:
        return self.freshness is Freshness.FRESH


def _expires_at(donor: DonorModel) -> datetime | None:
    if donor.metrics_refreshed_at is None:
        return None
    return donor.metrics_refreshed_at + timedelta(days=filters_cfg.METRICS_TTL_DAYS)


def _by_metrics(statement: Select[Any], filters: DonorFilters, now: datetime) -> Select[Any]:
    """Фильтры колонок с числами Ahrefs: вердикт, DR, трафик, гео, данные."""
    if filters.status is not None:
        statement = statement.where(DonorModel.status == filters.status)
    if filters.min_dr is not None:
        statement = statement.where(DonorModel.dr >= filters.min_dr)
    if filters.min_traffic is not None:
        statement = statement.where(DonorModel.org_traffic >= filters.min_traffic)
    if filters.geo:
        statement = statement.where(DonorModel.geo == filters.geo.strip().lower())
    if filters.freshness is not None:
        statement = statement.where(_freshness_is(filters.freshness, now))
    return statement


def _narrow(statement: Select[Any], filters: DonorFilters, now: datetime) -> Select[Any]:
    if filters.ids:
        statement = statement.where(DonorModel.id.in_(filters.ids))
    statement = _by_metrics(statement, filters, now)
    if filters.search:
        needle = f"%{filters.search.strip().lower()}%"
        statement = statement.where(
            or_(DomainModel.host.ilike(needle), DonorModel.reject_reason.ilike(needle))
        )
    if filters.has_contact is not None:
        found = DonorModel.contact_status == ContactStatus.FOUND
        # «Нет адреса» — это и «искали, не нашли», и «ещё не искали». У второго
        # исход пуст, а NULL в SQL ни равен, ни не равен «найден»: голое
        # отрицание теряло всех, кого ещё не искали.
        missing = or_(DonorModel.contact_status.is_(None), ~found)
        statement = statement.where(found if filters.has_contact else missing)
    return statement


class DonorBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _screen(*columns: Any) -> Select[Any]:
        """Основа экрана: донор с доменом — только доноры (`standing.is_donor`).

        Строки, их число, счётчики фильтров и выгрузка берут её одну: счётчик
        «всей базы» рядом со списком доноров назвал бы донорами и кандидатов.
        """
        return (
            select(*columns)
            .select_from(DonorModel)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(is_donor())
        )

    async def page(self, filters: DonorFilters, *, now: datetime | None = None) -> DonorPage:
        moment = now or datetime.now(UTC)
        rows = await self._session.execute(
            _narrow(
                self._screen(DonorModel, DomainModel.host, freshness(moment).label("freshness")),
                filters,
                moment,
            )
            .order_by(DonorModel.dr.desc().nullslast(), DonorModel.id.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )
        found = rows.all()
        total = await self._session.scalar(
            _narrow(self._screen(func.count(DonorModel.id)), filters, moment)
        )
        contacts = await self._contact_counts([donor.domain_id for donor, _, _ in found])
        return DonorPage(
            rows=[
                DonorRow(
                    donor=donor,
                    host=host,
                    contacts=contacts.get(donor.domain_id, 0),
                    freshness=Freshness(state),
                )
                for donor, host, state in found
            ],
            total=int(total or 0),
        )

    async def picked(
        self, ids: Sequence[int], *, limit: int, now: datetime | None = None
    ) -> Picked:
        """Отмеченные доноры — независимо от фильтра на экране.

        Номер, которого не может быть в базе (за пределом столбца), — такой
        же «записи нет», как любой другой несуществующий.
        """
        asked = list(dict.fromkeys(ids))
        known = tuple(one for one in asked if storable(one))
        page = await self.page(DonorFilters(ids=known, limit=limit), now=now) if known else None
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

    async def facets(self) -> Facets:
        """Счётчики под фильтрами — по всем донорам экрана."""
        moment = datetime.now(UTC)
        statuses = await self._session.execute(
            self._screen(DonorModel.status, func.count(DonorModel.id)).group_by(DonorModel.status)
        )
        countries = await self._session.execute(
            self._screen(DonorModel.geo, func.count(DonorModel.id))
            .where(DonorModel.geo.is_not(None))
            .group_by(DonorModel.geo)
        )
        # Через подзапрос, а не `group_by(выражение)`: граница срока едет
        # параметром, и выражение в списке колонок и в группировке вышло бы
        # с разными номерами параметров — базе они разные.
        states = self._screen(freshness(moment).label("state")).subquery()
        by_state = await self._session.execute(
            select(states.c.state, func.count()).group_by(states.c.state)
        )
        return Facets(
            statuses=dict(statuses.tuples().all()),
            countries={str(code).lower(): int(count) for code, count in countries.tuples()},
            freshness={Freshness(value): int(count) for value, count in by_state.tuples()},
        )

    async def card(self, donor_id: int) -> DonorCard:
        if not storable(donor_id):
            raise UnknownDonorError(f"Донора №{donor_id} нет")
        moment = datetime.now(UTC)
        rows = await self._session.execute(
            select(DonorModel, DomainModel.host, freshness(moment).label("freshness"))
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DonorModel.id == donor_id)
        )
        found = rows.first()
        if found is None:
            raise UnknownDonorError(f"Донора №{donor_id} нет")

        donor, host, state = found
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
            contacts=contacts,
            freshness=Freshness(state),
            expires_at=_expires_at(donor),
            contact_refusal=await search_refusal(self._session, donor),
            review_run=await decided_in(self._session, donor.domain_id, donor.review),
            letter=await Recipients(self._session).letter_address(donor.domain_id),
            removal=await removal_refusals(self._session, contacts),
        )

    async def _contact_counts(self, domain_ids: Sequence[int]) -> dict[int, int]:
        if not domain_ids:
            return {}
        rows = await self._session.execute(
            select(ContactModel.domain_id, func.count(ContactModel.id))
            .where(ContactModel.domain_id.in_(domain_ids))
            .group_by(ContactModel.domain_id)
        )
        return dict(rows.tuples().all())
