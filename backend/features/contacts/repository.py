"""Хранение исхода поиска контакта.

Два запроса, и оба про идемпотентность. `pending_hosts` отбирает доноров,
по которым за контактом ещё не ходили или ходили давно, — иначе повторный
запуск платил бы за уже пройденные домены. `save` пишет и адрес, и исход
попытки вместе с отметкой времени: без отметки нельзя отличить «не нашли»
от «ещё не искали» (okf/unchecked-vs-unsuitable.md).

Адрес принадлежит домену, а не донору: на Этапе 2 тот же сайт выступает
рекламодателем, и второй раз его контакт искать незачем.

Правило «кому искать» здесь одно (`_needs_contact`) и работает в двух
масштабах: общая очередь и один донор с его карточки. Рядом — оно же
словами (`refusal_of`): экран говорит, почему поиск не ставится, до нажатия,
а не отказом после.

**Адрес, вписанный человеком, закрывает поиск** (26.09.2026). Лестница
кончается платной ступенью, а лучше адреса, который человек взял у самого
донора, она не найдёт: такого донора общий поиск не берёт — ни в первый
раз, ни по истечении срока, — и его исход проход не перезаписывает
(`manual_address`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.donors.standing import donor_now, is_donor

logger = logging.getLogger(__name__)

#: Исходы, которые повторяются при следующем прогоне: мы не спросили, а не
#: узнали, что контакта нет.
RETRIABLE = frozenset({ContactStatus.NO_QUOTA, ContactStatus.RATE_LIMITED, ContactStatus.ERROR})


def manual_address() -> ColumnElement[bool]:
    """У донора есть адрес, вписанный человеком (`ContactSource.MANUAL`).

    Так же записан и адрес, с которого донор ответил (`replies`): он тоже
    пришёл от самого донора, и лестница его не улучшит.
    """
    return (
        select(ContactModel.id)
        .where(ContactModel.domain_id == DonorModel.domain_id)
        .where(ContactModel.source == ContactSource.MANUAL)
        .exists()
    )


def _needs_contact(border: datetime) -> ColumnElement[bool]:
    """Кому пора искать контакт: принятым человеком донорам без свежей попытки.

    Только принятым: контакт ищется после решения человека, а не до. Прогон
    23.09.2026 искал адреса всем «годным» по порогам — и нашёл
    `copyright@x.com` и `weee@microsoft.com`. Со скрейпером это время,
    с платным сервисом — деньги за каждый бренд и госсайт.

    И без вписанного руками адреса: он лучше всего, что найдёт лестница,
    а её последняя ступень платная.
    """
    return and_(
        DonorModel.status == DonorStatus.SUITABLE,
        is_donor(),
        ~manual_address(),
        or_(
            DonorModel.contact_attempted_at.is_(None),
            DonorModel.contact_attempted_at < border,
            DonorModel.contact_status.in_(tuple(RETRIABLE)),
        ),
    )


class SearchRefusedError(ValueError):
    """Поиск адреса этому донору сейчас не ставится. Текст говорит почему."""


#: Отказ, который правило вынесло, а объяснение не нашло. Так быть не должно:
#: `refusal_of` читает вслух то же правило, что `_needs_contact` проверяет
#: запросом. Если случилось — отказ всё равно честный, а расхождение в журнале.
UNEXPLAINED = "Донор не ждёт поиска адреса по общему правилу — причину назвать не удалось."


def _not_entitled(donor: DonorModel) -> str | None:
    """Кому поиск не положен вовсе, пока не изменится сам донор: не подходит
    по порогам или не принят человеком. Первые два условия `_needs_contact`."""
    if donor.status is DonorStatus.UNCHECKED:
        return "Адрес ищут только подходящим донорам, а этого пороги ещё не проверяли."
    if donor.status is not DonorStatus.SUITABLE:
        return "Адрес ищут только подходящим донорам — этот не прошёл пороги."
    if donor.review == "rejected":
        return "Донора отклонил человек — адрес ему не ищут."
    if not donor_now(donor):
        return (
            "Адрес ищут после решения человека: донора сначала принимают на рассмотрении прогона."
        )
    return None


#: Почему не ищут донору с вписанным руками адресом.
MANUAL_REFUSAL = (
    "Адрес вписан человеком — лестницей его не ищут: лучше она не найдёт, "
    "а её последняя ступень платная."
)


def refusal_of(
    donor: DonorModel,
    *,
    now: datetime,
    ttl_days: int = cfg.CONTACT_TTL_DAYS,
    manual: bool = False,
) -> str | None:
    """Почему донору сейчас не ищут адрес — словами для экрана. `None` — ищут.

    Это `_needs_contact`, прочитанное вслух, условие за условием и в том же
    порядке. Решает всё равно запрос (`search_refusal`): здесь только имя
    невыполненного условия. Разойтись им не даёт тест, прогоняющий обе
    стороны по всем сочетаниям состояний донора. `manual` — есть ли у донора
    вписанный руками адрес (`manual_address`).

    Исход прошлого поиска здесь не пересказывается: карточка показывает его
    сама, значком и датой. Отказ отвечает на другой вопрос — почему нельзя
    сейчас и когда станет можно.
    """
    never = _not_entitled(donor)
    if never is not None:
        return never
    if manual:
        return MANUAL_REFUSAL
    attempted = donor.contact_attempted_at
    if attempted is None or donor.contact_status in RETRIABLE:
        return None
    # Строго «раньше»: запрос берёт попытку старше границы (`<`), и на самой
    # границе донор ещё не ждёт — иначе экран обещал бы поиск, а сервер отказал.
    again = attempted + timedelta(days=ttl_days)
    if again < now:
        return None
    return (
        f"Повторный поиск — не раньше {again:%d.%m.%Y}: до тех пор исход прошлого "
        "считается свежим, а повтор прошёл бы ту же лестницу вплоть до платной ступени."
    )


class ContactQueue(Protocol):
    """Очередь на поиск контакта.

    Лестница одна на оба этапа, а очередей две: доноры и рекламодатели.
    Протокол ровно поэтому — чтобы у поиска был один порядок работы,
    а не два, разъезжающихся на первой правке.
    """

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        """Кому пора искать контакт."""
        ...

    async def save(self, results: Sequence[LadderResult]) -> int:
        """Сохранить исходы. Возвращает число доменов с адресом."""
        ...


async def domain_ids(session: AsyncSession, hosts: Sequence[str]) -> dict[str, int]:
    """Номера доменов по хостам. Общее у обеих очередей."""
    rows = await session.execute(
        select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(list(hosts)))
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def save_addresses(
    session: AsyncSession, results: Sequence[LadderResult], ids: dict[str, int]
) -> int:
    """Записать найденные адреса. Общее у обеих очередей: адрес
    принадлежит домену, а не роли, в которой он выступает."""
    payload = [
        {"domain_id": ids[r.host], "email": r.contact.email, "source": r.contact.source}
        for r in results
        if r.contact is not None and r.host in ids
    ]
    if not payload:
        return 0

    statement = insert(ContactModel).values(payload)
    # Тот же адрес на том же домене — не ошибка: его мог найти прошлый
    # прогон. Обновляем источник: он мог стать дешевле.
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["domain_id", "email"],
            set_={"source": statement.excluded["source"]},
        )
    )
    return len(payload)


class ContactRepository:
    """Доступ к контактам доноров.

    `donor_id` сужает очередь до одного донора — поиск с его карточки. Правило
    «кому искать» при этом то же (`_needs_contact`) с одним условием больше:
    узкая очередь — не второй отбор, а тот же, и второй экземпляр правила
    разошёлся бы с первым на первой правке.
    """

    def __init__(self, session: AsyncSession, *, donor_id: int | None = None) -> None:
        self._session = session
        self._donor_id = donor_id

    def _waiting(self, border: datetime) -> ColumnElement[bool]:
        rule = _needs_contact(border)
        return rule if self._donor_id is None else and_(rule, DonorModel.id == self._donor_id)

    async def pending_hosts(
        self,
        *,
        limit: int = 100,
        ttl_days: int = cfg.CONTACT_TTL_DAYS,
        now: datetime | None = None,
    ) -> list[str]:
        """Подходящие доноры, которым пора искать контакт.

        Берём тех, по кому попытки не было вовсе, чья попытка устарела или
        кончилась нехваткой квоты. Домены с найденным адресом и свежим
        отказом не трогаем — за них уже заплачено.
        """
        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)

        rows = await self._session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(self._waiting(border))
            .order_by(DonorModel.dr.desc().nullslast())
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def pending_count(
        self, *, ttl_days: int = cfg.CONTACT_TTL_DAYS, now: datetime | None = None
    ) -> int:
        """Сколько доноров ждёт контакта. Тот же отбор, что и у `pending_hosts`:
        два разных правила «кому нужен контакт» разошлись бы на первой правке,
        и экран показывал бы одно число, а поиск брал другое."""
        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)
        return int(
            await self._session.scalar(
                select(func.count(DomainModel.host))
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(self._waiting(border))
            )
            or 0
        )

    async def save(self, results: Sequence[LadderResult], *, now: datetime | None = None) -> int:
        """Сохранить пачку исходов. Возвращает число доноров с адресом.

        Пачка — чекпоинт: сохранённые домены выпадают из повторного прогона,
        и падение на следующей пачке не стоит уже оплаченной работы.
        """
        if not results:
            return 0

        moment = now or datetime.now(UTC)
        ids = await domain_ids(self._session, [r.host for r in results])

        await self._save_statuses(results, ids, moment)
        return await save_addresses(self._session, results, ids)

    async def _save_statuses(
        self, results: Sequence[LadderResult], ids: dict[str, int], moment: datetime
    ) -> None:
        """Исход и отметка времени по каждому донору — их пара и есть
        идемпотентность.

        Донора, которому человек вписал адрес, пока шёл проход, исход прохода
        не перезаписывает: «адреса нет» рядом с вписанным адресом — неправда,
        и фильтр «с адресом» его бы потерял.
        """
        for result in results:
            domain_id = ids.get(result.host)
            if domain_id is None:
                continue
            await self._session.execute(
                update(DonorModel)
                .where(DonorModel.domain_id == domain_id)
                .where(~manual_address())
                .values(contact_status=result.status, contact_attempted_at=moment)
            )


async def search_refusal(
    session: AsyncSession, donor: DonorModel, *, now: datetime | None = None
) -> str | None:
    """Почему этому донору нельзя поставить поиск адреса; `None` — можно.

    Решает тот же запрос, что набирает общую очередь, суженный до донора:
    «можно» ровно тогда, когда общий поиск взял бы его сам. `refusal_of`
    только называет причину отказа — решение за ним не остаётся.
    """
    moment = now or datetime.now(UTC)
    if await ContactRepository(session, donor_id=donor.id).pending_count(now=moment):
        return None
    manual = await session.scalar(
        select(ContactModel.id)
        .where(ContactModel.domain_id == donor.domain_id)
        .where(ContactModel.source == ContactSource.MANUAL)
        .limit(1)
    )
    reason = refusal_of(donor, now=moment, manual=manual is not None)
    if reason is None:
        logger.warning(
            "контакты: донор №%s не ждёт поиска, а объяснение правила этого не видит — "
            "`refusal_of` разошёлся с `_needs_contact`",
            donor.id,
        )
        return UNEXPLAINED
    return reason
