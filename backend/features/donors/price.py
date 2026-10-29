"""Цена донора — одна запись на оба пути: из ответа и руками.

До 07.10.2026 цену в карточку клал только разбор ответа на письмо Этапа 1
(`replies/repository.store_price`), и поля писались прямо там. Цена, которую
человек знает сам (`manual_price.py`), ложится в те же поля: обход Этапа 2
(`crawl/targets.choose`), скоринг, перевод в рекламодатели и сборка офферов
читают `last_price*` и про источник не знают.

**Пишутся все поля разом, и источник — всегда.** Последняя цена обязана
говорить, откуда она: ручная заметка рядом с ценой из ответа или список цен
чужого ответа рядом с ручной ценой врали бы. Поэтому поле, которого у этого
источника нет, затирается пустым, а не остаётся от прежней цены.

**Последнее слово донора побеждает** (решение Anthony 11.10.2026). «Не продаём»
снимает цену — с ней домен уходит из офферов Этапа 2: они стоят на свежей цене
донора (`letters/recipients._fresh_price`, `niche_recipients._example_host`).
«Бесплатно» ставит вместо цены отметку — ответ донора на домене
(`domains.seller_answer`). Последнее — по тому, когда донор это сказал,
а не когда мы это разобрали: у ответа — когда он пришёл, у цены руками — когда её
вписали; это время и ложится в `last_price_at` и `seller_answer_at`. Слово старше
уже лежащего ничего не меняет — у всех путей записи одинаково: разбор ответа,
подтверждение человеком, «не продаёт» и цена руками.

До 11.10.2026 ответ без цены цену не трогал, а последним было последнее
записанное: старый ответ с ценой, подтверждённый человеком позже, возвращал
«продаёт» поверх «не продаём».

**С ценой ложится всё, что донор сказал рядом** (сверка ТЗ 12.10.2026: «белая цена,
серая цена, способы оплаты — и кладёт в ту же базу»): белая и серая раздельно, оплата,
срок публикации, тип ссылки и номер ответа (`PriceDetails`). До этого на донора
ложилась одна цена, а остальное оставалось у ответа. Правило «все поля разом» на них
то же: у цены руками срока и ответа нет — и прежние из ответа затираются.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from backend.features.core.domain import PriceSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.replies.extract import WITHOUT_PRICE

#: Колонки цены донора — все, что пишет `put_price`. Снимает их «не продаём» тоже все
#: разом (`_drop_price`): оплата чужого ответа рядом с пустой ценой врала бы так же,
#: как список цен.
PRICE_COLUMNS: tuple[str, ...] = (
    "last_price",
    "last_price_currency",
    "last_price_at",
    "last_offers",
    "last_price_source",
    "last_price_note",
    "last_price_by",
    "last_price_white",
    "last_price_grey",
    "last_payment_methods",
    "last_placement_days",
    "last_link_type",
    "last_price_reply_id",
)


@dataclass(frozen=True, slots=True)
class PriceDetails:
    """Всё, что пишется с ценой, кроме неё самой: что сказал донор и откуда она.

    У цены из ответа — его поля (`replies/donor_card.reply_details`): белая и серая,
    оплата, срок, тип ссылки, номер ответа. У цены руками — белая (её и вписали),
    серая и оплата, если их вписали, заметка «откуда цена» и автор.
    """

    white: Decimal | None = None
    grey: Decimal | None = None
    payment_methods: tuple[str, ...] = ()
    #: Срок публикации в днях и `dofollow` / `nofollow` — словами донора.
    placement_days: int | None = None
    link_type: str | None = None
    #: Ответ, из которого цена; пусто — цена руками.
    reply_id: int | None = None
    #: Заметка «откуда цена» и кто её вписал — у цены руками.
    note: str | None = None
    by: str | None = None


#: Цена без подробностей.
NO_DETAILS = PriceDetails()


def said_later(donor: DonorModel | None, domain: DomainModel | None, at: datetime) -> bool:
    """Лежит ли слово новее `at`: цена — или «не продаём», «бесплатно» на домене.
    Тогда цене, названной в `at`, в карточку не ложиться. Донора ещё нет — смотрится домен:
    загрузка списком (`bulk_entry.py`) судит строку до того, как завести донора."""
    if donor is not None and donor.last_price is not None and _after(donor.last_price_at, at):
        return True
    return (
        domain is not None
        and domain.seller_answer in WITHOUT_PRICE
        and _after(domain.seller_answer_at, at)
    )


def put_price(
    donor: DonorModel,
    *,
    amount: Decimal,
    currency: str | None,
    at: datetime,
    source: PriceSource,
    offers: list[dict[str, Any]] | None = None,
    details: PriceDetails = NO_DETAILS,
) -> None:
    """Записать последнюю цену донора вместе с тем, откуда она.

    `amount` — главная цена: белая, а без неё серая. `at` — когда цену назвали. Слово
    новее `at` проверяет вызывающий (`said_later`): ответ и цена руками отвечают на него
    по-разному — цена из ответа молча не ложится, ручной — отказ словами.
    `offers` — все цены того же ответа (у ручной цены ответа нет — пусто);
    `details` — что донор сказал рядом с ценой и откуда она (`PriceDetails`).
    """
    donor.last_price = amount
    donor.last_price_currency = currency
    donor.last_price_at = at
    donor.last_offers = offers
    donor.last_price_source = source.value
    donor.last_price_note = details.note
    donor.last_price_by = details.by
    donor.last_price_white = details.white
    donor.last_price_grey = details.grey
    donor.last_payment_methods = list(details.payment_methods) or None
    donor.last_placement_days = details.placement_days
    donor.last_link_type = details.link_type
    donor.last_price_reply_id = details.reply_id


def put_answer(
    domain: DomainModel,
    donor: DonorModel | None,
    *,
    answer: str,
    reply_id: int | None,
    at: datetime,
) -> bool:
    """Ответ донора — на домен; «не продаём» и «бесплатно» снимают цену, названную
    не позже `at`. `False` — на домене ответ новее, этот не лёг.

    Цена снимается и тогда, когда сам ответ не лёг: «150 $», «не продаём»,
    «продаём, цену пришлю» — цены нет, хоть последним и было «продаём».
    """
    if answer in WITHOUT_PRICE and donor is not None:
        _drop_price(donor, at)
    if _after(domain.seller_answer_at, at):
        return False
    domain.seller_answer = answer
    domain.seller_answer_at = at
    domain.seller_answer_reply_id = reply_id
    return True


def _drop_price(donor: DonorModel, at: datetime) -> None:
    """Снять цену, названную не позже `at`, — всеми полями, как она пишется
    (`PRICE_COLUMNS`): серая, оплата и срок без цены остались бы словами ответа,
    который последним уже не был."""
    if donor.last_price is None or _after(donor.last_price_at, at):
        return
    for column in PRICE_COLUMNS:
        setattr(donor, column, None)


def _after(stored: datetime | None, at: datetime) -> bool:
    """Записанное позже `at`. Без отметки — не позже: сравнивать не с чем."""
    return stored is not None and stored > at
