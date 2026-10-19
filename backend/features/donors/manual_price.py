"""Цена, которую человек знает сам, и донор, заведённый вручную.

Требование Этапа 2: «по кому запускаем — только доноры с известной ценой (из
базы или заведённые вручную)». До 07.10.2026 цена попадала к донору только из
разобранного ответа на письмо Этапа 1, а агентство знает цены многих сайтов
само — свой прайс, биржи вроде LinkDetective, прошлые сделки. Без ручного ввода
Этап 2 по ним не начать.

**Те же поля, что у цены из ответа** (`price.put_price`): обход
(`crawl/targets.choose`), скоринг, перевод в рекламодатели и сборка офферов
работают с ручной ценой без единой правки. Рядом — откуда она (`manual`), кто
её указал и заметка «откуда цена»; списка цен ответа у неё нет. Вписанная цена —
белая; серую и способы оплаты человек вписывает рядом, если знает (сверка ТЗ
12.10.2026: «белая цена, серая цена, способы оплаты — в ту же базу»).

**Один путь на три входа**: карточка донора («Указать цену»), панель «Обход
доноров» («Завести донора вручную») и консоль (`outreach donor-add`). Домен,
который ещё не донор, становится им: принятым человеком, как в очереди прогона,
но без прогона — пометка `entered_by`. Ни Ahrefs, ни другой платный сервис не
зовутся: метрик у такого донора нет, пока его не найдёт прогон, а «годен» у
него — слово человека, как у липового донора (`probe.py`).

**Отказы — словами и до записи**, ничего не меняя:

* не домен; наш домен рассылки; гос. или учебная зона, платформа или соцсеть,
  крупное СМИ, справочник (`runs/exclusions.by_name` — то же правило не пускает
  домен в прогон) и DR > 80 по замеру — «кому не пишем» ТЗ (11.10.2026);
* стоп-лист, поставщик агентства, отклонён человеком (`Exclusions` прогона).
  Молчание в ответ на письмо и «не продаём» — не отказ: цену человек знает
  не из письма;
* кандидат, ждущий решения в очереди прогона, — решают там, а не здесь;
* не прошёл пороги отбора — Этап 2 обходит только годных доноров;
* у донора слово новее этой минуты (`price.said_later`, 11.10.2026): последнее
  слово сильнее. Цена руками — слово минуты, когда её вписали, и такого почти
  не бывает; бывает — часы разошлись, и записи о цене, которой нет, не будет.

Отказ по состоянию домена карточка донора называет до нажатия — теми же
словами (`price_refusal`, 08.10.2026).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, DonorStatus, PriceSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.host import normalize_host
from backend.features.donors.price import PriceDetails, put_price, said_later
from backend.features.donors.price_refusal import price_refusal
from backend.features.donors.standing import UnknownDonorError, donor_now
from backend.features.replies.money import (
    CASE_SENSITIVE,
    CURRENCIES,
    IMPLAUSIBLE_PRICE,
    normalize_currency,
)
from backend.features.review.candidates import Decision
from backend.shared.database.ids import storable
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Заметка «откуда цена» — не длиннее колонки.
MAX_NOTE = 200
#: Почта того, кто указал цену, — не длиннее колонки.
MAX_AUTHOR = 255
#: Валюта, если её не назвали: так названо большинство цен рынка.
DEFAULT_CURRENCY = "USD"
#: Цена хранится до сотых (`DECIMAL(10, 2)`): лишний знак база округлила бы молча.
CENTS = Decimal("0.01")
#: Способ оплаты — не длиннее, чем их кладёт разбор ответа (`extract.parse_form`).
MAX_PAYMENT_METHOD = 64
#: Способов оплаты у цены — не больше: длиннее список — уже не способы, а заметка.
MAX_PAYMENT_METHODS = 10
#: Чем разделяют способы оплаты, вписанные одной строкой: «PayPal, Wise; карта».
#: В скобках — не делят: «Bank transfer (SWIFT, SEPA)» — один способ (`_methods_of`).
_PAYMENT_SEPARATORS = frozenset(",;\n")
#: Коды, которые знает разбор ответов (`replies/money.py`). Незнакомое слово
#: `normalize_currency` вернула бы как есть, и опечатка «UDS» легла бы валютой
#: цены, а «kr» и «pesos» — кодом без страны.
KNOWN_CURRENCIES: frozenset[str] = frozenset(CURRENCIES.values()) | frozenset(
    CASE_SENSITIVE.values()
)

#: Как писать цену и валюту — одной подсказкой у каждого отказа ввода.
_PRICE_HINT = "впишите число больше нуля, например 150 или 150.50"
_CURRENCY_HINT = "впишите код (USD, EUR, GBP) или знак ($, €, £)"


class ManualPriceError(ValueError):
    """Ввод не годится: не цена, не валюта, не домен. Текст говорит почему."""


class DonorRefusedError(ManualPriceError):
    """Домену цену вручную не записываем: стоп-лист, поставщик, решение человека,
    пороги. Текст называет причину и что сделать."""


@dataclass(frozen=True, slots=True)
class ManualPrice:
    """Цена, которую человек знает сам, — проверенная."""

    amount: Decimal
    currency: str
    #: Откуда цена: «прайс агентства», «LinkDetective». Пусто — не сказали.
    note: str | None
    #: Кто указал — почта сотрудника.
    by: str
    #: Серая цена — без пометки «реклама», в той же валюте. Пусто — не вписали.
    grey: Decimal | None = None
    #: Способы оплаты, как их вписали: «PayPal», «Wise». Пусто — не вписали.
    payment_methods: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Entered:
    """Что вышло из ручного ввода."""

    donor_id: int
    host: str
    #: Донором домен стал сейчас; `False` — уже был донором, записана цена.
    created: bool
    #: Годен по порогам — обход Этапа 2 его берёт. Донор, принятый раньше, мог
    #: стать негодным после обновления метрик: цена записана, обхода не будет.
    suitable: bool


def manual_price(
    amount: object,
    currency: object = DEFAULT_CURRENCY,
    note: object = None,
    *,
    by: str,
    grey: object = None,
    payment: object = None,
) -> ManualPrice:
    """Проверить то, что вписал человек. Отказ — словами, до записи. Серая цена —
    по правилам цены (`typed_amount`), оплата — списком или строкой через запятую."""
    return ManualPrice(
        amount=typed_amount(amount),
        currency=typed_currency(currency),
        note=_note(note),
        by=_author(by),
        grey=_grey(grey),
        payment_methods=typed_payment(payment),
    )


def _grey(raw: object) -> Decimal | None:
    """Серая цена: пусто — не вписали; вписанное — цена по тем же правилам."""
    if raw is None or not str(raw).strip():
        return None
    try:
        return typed_amount(raw)
    except ManualPriceError as exc:
        raise ManualPriceError(f"Серая цена — {exc}") from None


def _methods_of(text: str) -> list[str]:
    """Строка — способами: по запятой, точке с запятой и переводу строки, но не внутри
    скобок — «Bank transfer (SWIFT, SEPA), PayPal» — два способа, а не три."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        depth += (char in "([") - (char in ")]" and depth > 0)
        if depth == 0 and char in _PAYMENT_SEPARATORS:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def typed_payment(raw: object) -> tuple[str, ...]:
    """Способы оплаты, вписанные человеком: список или строка через запятую.

    Каждый — одной строкой, без повторов и не длиннее, чем их кладёт разбор ответа:
    в карточке донора способ из ответа и вписанный руками стоят в одном ряду. Правило
    одно у цены руками и у подтверждения разбора (`replies/confirmation.py`).
    """
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list | tuple) else []
    said = [
        " ".join(part.split())
        for item in items
        if isinstance(item, str)
        for part in _methods_of(item)
    ]
    methods = tuple(dict.fromkeys(method for method in said if method))
    long = next((method for method in methods if len(method) > MAX_PAYMENT_METHOD), None)
    if long is not None:
        raise ManualPriceError(
            f"«{long[:40]}…» — не способ оплаты: не длиннее {MAX_PAYMENT_METHOD} знаков, "
            "например PayPal, Wise, карта."
        )
    if len(methods) > MAX_PAYMENT_METHODS:
        raise ManualPriceError(
            f"Способов оплаты вписано {len(methods)} — не больше {MAX_PAYMENT_METHODS}: "
            "остальное — в «Откуда цена»."
        )
    return methods


def typed_amount(raw: object) -> Decimal:
    """Число, вписанное человеком, — в цену. Запятая — отказ, а не догадка: «1,200»
    бывает и тысячей двумястами, и единицей с копейками.

    Правило одно на оба входа цены руками: «Указать цену» и подтверждение разбора
    ответа (`replies/confirmation.py`) кладут число в те же поля карточки донора.
    """
    said = "" if raw is None else str(raw).strip()
    if not said:
        raise ManualPriceError(f"Цена не указана: {_PRICE_HINT}.")
    try:
        value = raw if isinstance(raw, Decimal) else Decimal("".join(said.split()))
    except InvalidOperation:
        raise ManualPriceError(f"«{said}» — не цена: {_PRICE_HINT}.") from None
    if not value.is_finite() or value <= 0:
        raise ManualPriceError(f"«{said}» — не цена: {_PRICE_HINT}.")
    if value >= IMPLAUSIBLE_PRICE:
        ceiling = spaced(int(IMPLAUSIBLE_PRICE))
        raise ManualPriceError(
            f"Цена {said} — не меньше {ceiling}: за одно размещение столько не платят, "
            "проверьте число."
        )
    if value != value.quantize(CENTS):
        raise ManualPriceError(f"Цена {said} точнее копеек: хранится два знака после точки.")
    return value.quantize(CENTS)


def typed_currency(raw: object) -> str:
    """Валюта, вписанная человеком, — к коду, который знает разбор ответов. Пусто
    или незнакомо — отказ: правило то же у подтверждения разбора, что у `typed_amount`."""
    said = raw.strip() if isinstance(raw, str) else ""
    code = normalize_currency(said)
    if code is None:
        raise ManualPriceError(f"Не указана валюта цены: {_CURRENCY_HINT}.")
    if code not in KNOWN_CURRENCIES:
        raise ManualPriceError(f"Валюта «{said}» не знакома или неоднозначна: {_CURRENCY_HINT}.")
    return code


def _note(raw: object) -> str | None:
    """Заметка одной строкой: переносы и двойные пробелы — один пробел."""
    text = "" if raw is None else " ".join(str(raw).split())
    if len(text) > MAX_NOTE:
        raise ManualPriceError(
            f"«Откуда цена» — не длиннее {MAX_NOTE} знаков, а вписано {len(text)}."
        )
    return text or None


def _author(raw: str) -> str:
    who = raw.strip()
    name, at, domain = who.partition("@")
    if not (name and at and domain) or len(who) > MAX_AUTHOR:
        raise ManualPriceError(
            f"«{who}» — не почта сотрудника: цену записывают от имени того, кто её знает."
        )
    return who


async def enter_host(
    session: AsyncSession,
    raw_host: str,
    price: ManualPrice,
    *,
    author_id: int | None = None,
    now: datetime | None = None,
) -> Entered:
    """«Завести донора вручную»: домен — к корню, как у прогона (`host.py`),
    дальше общий путь `enter`. Без коммита."""
    host = normalize_host(raw_host)
    if not host:
        raise ManualPriceError(
            f"«{raw_host.strip()}» — не домен: впишите адрес сайта, например example.com."
        )
    return await enter(session, host, price, author_id=author_id, now=now)


async def price_donor(
    session: AsyncSession,
    donor_id: int,
    price: ManualPrice,
    *,
    author_id: int | None = None,
    now: datetime | None = None,
) -> Entered:
    """«Указать цену» с карточки — тем же путём, по домену карточки. Домен не
    приводится к корню заново: он уже ключ базы. Без коммита."""
    host = (
        await session.scalar(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DonorModel.id == donor_id)
        )
        if storable(donor_id)
        else None
    )
    if host is None:
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    return await enter(session, host, price, author_id=author_id, now=now)


async def enter(
    session: AsyncSession,
    host: str,
    price: ManualPrice,
    *,
    author_id: int | None = None,
    now: datetime | None = None,
) -> Entered:
    """Записать ручную цену донору домена; домен ещё не донор — сделать донором.

    Отказ — до первой записи, словами `price_refusal`. Без коммита: коммитит
    вызывающий, вместе с записью в журнал.
    """
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    donor = (
        None
        if domain is None
        else await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    )
    refusal = await price_refusal(session, host, donor)
    if refusal is not None:
        raise DonorRefusedError(refusal)
    moment = now or datetime.now(UTC)
    if donor is not None and donor_now(donor):
        return await _priced(
            session, donor, host, price, created=False, author_id=author_id, at=moment
        )
    made = await _accepted(session, domain, donor, host=host, by=price.by, at=moment)
    return await _priced(session, made, host, price, created=True, author_id=author_id, at=moment)


async def _accepted(
    session: AsyncSession,
    domain: DomainModel | None,
    donor: DonorModel | None,
    *,
    host: str,
    by: str,
    at: datetime,
) -> DonorModel:
    """Донор, принятый человеком без прогона: домен и строка донора — новые или прежние."""
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    if donor is None:
        donor = DonorModel(domain_id=domain.id)
        session.add(donor)
    # «Годен» — слово человека, а не порогов: метрики платные, и ручной ввод
    # их не покупает. Найдёт донора прогон — рассудит пороги, как у любого.
    donor.status = DonorStatus.SUITABLE
    donor.review, donor.review_at, donor.review_by = Decision.ACCEPTED.value, at, by
    donor.entered_by = by
    await session.flush()
    return donor


async def _priced(
    session: AsyncSession,
    donor: DonorModel,
    host: str,
    price: ManualPrice,
    *,
    created: bool,
    author_id: int | None,
    at: datetime,
) -> Entered:
    """Цена — в карточку, запись — в журнал: кто, сколько, откуда и что было до.

    Правило последнего слова — то же, что у цены из ответа (`price.said_later`): цена
    руками — слово минуты, когда её вписали, и обычно новее всего. Слово новее этой
    минуты — отказ словами, а не запись в журнал о цене, которой нет.
    """
    before = _shown(donor)
    if said_later(donor, await session.get(DomainModel, donor.domain_id), at):
        raise DonorRefusedError(
            f"Цену {host} не записали: у донора записано слово новее этой цены — ответ или "
            "цена позже неё, а последнее слово сильнее. Обновите карточку и проверьте цену."
        )
    put_price(
        donor,
        amount=price.amount,
        currency=price.currency,
        at=at,
        source=PriceSource.MANUAL,
        details=PriceDetails(
            white=price.amount,
            grey=price.grey,
            payment_methods=price.payment_methods,
            note=price.note,
            by=price.by,
        ),
    )
    await session.flush()
    await AccessRepository(session).record(
        AuditAction.PRICE_REVIEWED,
        author_id=author_id,
        target=f"donor:{donor.id}",
        details={
            "действие": "донор заведён вручную, с ценой" if created else "цена указана вручную",
            "донор": host,
            "цена": str(price.amount),
            "валюта": price.currency,
            "серая": None if price.grey is None else str(price.grey),
            "оплата": list(price.payment_methods) or None,
            "откуда цена": price.note,
            "кто": price.by,
            "прежняя цена": before,
        },
    )
    logger.info(
        "цена руками: донор №%s (%s) — %s %s, заведён сейчас: %s",
        donor.id,
        host,
        price.amount,
        price.currency,
        "да" if created else "нет",
    )
    return Entered(
        donor_id=donor.id,
        host=host,
        created=created,
        suitable=donor.status is DonorStatus.SUITABLE,
    )


def _shown(donor: DonorModel) -> str | None:
    """Прежняя цена для журнала: «120.00 EUR, из ответа». Пусто — цены не было."""
    if donor.last_price is None:
        return None
    source = "вручную" if donor.last_price_source == PriceSource.MANUAL else "из ответа"
    amount = " ".join(part for part in (str(donor.last_price), donor.last_price_currency) if part)
    return f"{amount}, {source}"
