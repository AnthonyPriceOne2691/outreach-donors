"""Доноры с ценами списком — тем же правилом, что «Завести донора вручную».

Загрузка таблицей (`bulk_file.py`) приносит до десяти тысяч строк, и каждая идёт ровно тем
путём, что строка формы: домен — к корню (`host.normalize_host`), цена, валюта и «откуда цена» —
проверкой ручного ввода (`manual_price.manual_price`), отказы — словами `price_refusal`: наш
домен рассылки, «кому не пишем», стоп-лист, поставщик, отклонён человеком, ждёт решения в очереди
прогона, не прошёл пороги, у донора слово новее этой цены. Запись — теми же кусками, что у кнопки
(`manual_record.py`): принят человеком без прогона, цена в карточку, строка журнала о цене
с пометкой «загрузка списком».

**Не построчно в базу.** Одна проверка ручного ввода — с десяток запросов, а строк — тысячи:
отказы считаются запросом на все домены (`price_refusals`), домены и доноры читаются и
пишутся пачкой — вставками списком, журнал — одной вставкой (`record_many`). Число запросов от
числа строк не растёт.

**В запросе, а не фоновой задачей** (замер 29.10.2026 на машине разработки, 10 000 строк —
предел загрузки): предпросмотр — 0,7 с, запись новых — 2,3 с, новой цены известным — 2,8 с,
из них процессора процесса API — 0,5 и 1,5–1,7 с. Разбор и подготовка записи уступают цикл
событий каждые `YIELD_EVERY` строк: остальные запросы API за это время не стоят. Файлы
в десятки строк — доли секунды. Станут файлы больше предела или запись дольше — фоновой
задачей с ходом на экране.

**Отчёт — по каждой строке**: заведён, цена обновлена или пропущен — и почему, теми же словами,
что сказала бы форма. Повтор домена в файле — пропуск со ссылкой на строку, где он уже был
(«www.example.com/path» и «example.com» — один домен). Строка без домена, не домен, не цена — тоже
пропуск с номером. Записанный донор, которого обход не возьмёт (уже был донором и по порогам не
годен), назван отдельно: цена у него есть, обхода не будет.

Предпросмотр — то же самое без записи (`write=False`): человек видит отчёт до нажатия, а запись
проверяет всё заново — между ними состояние доменов могло смениться.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.bulk_file import Column, Line
from backend.features.donors.host import normalize_host
from backend.features.donors.manual_price import (
    DEFAULT_CURRENCY,
    ManualPrice,
    ManualPriceError,
    manual_price,
    not_a_domain,
)
from backend.features.donors.manual_record import (
    accept_by_hand,
    later_refusal,
    price_details,
    shown_price,
    suitable,
    write_price,
)
from backend.features.donors.price import said_later
from backend.features.donors.price_refusal import price_refusals
from backend.features.donors.standing import donor_now

logger = logging.getLogger(__name__)

#: Строк разбора между уступками циклу событий (`_checked`).
YIELD_EVERY = 500
#: Как цена пришла — в строку журнала о цене: не кнопкой, а файлом.
VIA = "загрузка списком"
#: Записан, но обход его не возьмёт — словами формы (`ManualDonor.enteredSummary`).
UNSUITABLE = "цена записана, но по порогам отбора он не годен — обход Этапа 2 его не возьмёт"


class Outcome(StrEnum):
    """Что вышло со строкой."""

    CREATED = "created"  # донором стал сейчас
    PRICED = "priced"  # уже был донором — записана цена
    SKIPPED = "skipped"  # не записано ничего — почему, сказано


@dataclass(frozen=True, slots=True)
class Row:
    """Строка отчёта: номер строки файла, домен как написан, итог и почему."""

    line: int
    given: str
    outcome: Outcome
    host: str | None = None
    reason: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    #: Записан, годен и с ценой — обход его возьмёт (галочка «поставить в обход»).
    crawlable: bool = False


@dataclass(frozen=True, slots=True)
class Report:
    """Отчёт загрузки по всем строкам — в порядке файла."""

    rows: tuple[Row, ...]

    def count(self, outcome: Outcome) -> int:
        return sum(1 for row in self.rows if row.outcome is outcome)

    @property
    def crawl_hosts(self) -> list[str]:
        """Записанные доноры, которых возьмёт обход, — для очереди загрузки."""
        return [row.host for row in self.rows if row.crawlable and row.host is not None]


@dataclass(frozen=True, slots=True)
class _Entry:
    """Строка, прошедшая проверку ввода: домен уже к корню, цена проверена."""

    line: Line
    host: str
    price: ManualPrice


@dataclass(slots=True)
class _Write:
    """Строка, которую запишем: донор и домен — прежние или пока пустые."""

    entry: _Entry
    created: bool
    donor: DonorModel | None
    domain: DomainModel | None


async def enter_many(
    session: AsyncSession,
    lines: Sequence[Line],
    *,
    by: str,
    author_id: int | None = None,
    now: datetime | None = None,
    write: bool = False,
) -> Report:
    """Проверить строки и — с `write` — записать. Без коммита: коммитит вызывающий."""
    moment = now or datetime.now(UTC)
    entries, rows = await _checked(lines, by=by)
    hosts = [entry.host for entry in entries]
    domains, donors = await _known(session, hosts)
    refused = await price_refusals(session, {host: donors.get(host) for host in hosts})
    accepted: list[_Write] = []
    for entry in entries:
        donor, domain = donors.get(entry.host), domains.get(entry.host)
        why = refused.get(entry.host)
        if why is None and said_later(donor, domain, moment):
            why = later_refusal(entry.host)
        if why is not None:
            rows.append(_skipped(entry.line, why, host=entry.host))
            continue
        created = not (donor is not None and donor_now(donor))
        accepted.append(_Write(entry, created, donor, domain))
    if write:
        await _write(session, accepted, by=by, author_id=author_id, at=moment)
    rows += [_entered(item) for item in accepted]
    report = Report(tuple(sorted(rows, key=lambda row: row.line)))
    if write:
        logger.info(
            "доноры списком: заведено %s, цена обновлена %s, пропущено %s — %s",
            report.count(Outcome.CREATED),
            report.count(Outcome.PRICED),
            report.count(Outcome.SKIPPED),
            by,
        )
    return report


async def _checked(lines: Sequence[Line], *, by: str) -> tuple[list[_Entry], list[Row]]:
    """Ввод строк — правилами формы: домен, цена, валюта. Годные — первым вхождением домена.
    Каждые `YIELD_EVERY` строк — уступить цикл событий: разбор десяти тысяч строк — это
    процессор, и API в это время отвечал бы только этой загрузке."""
    entries: list[_Entry] = []
    skipped: list[Row] = []
    seen: dict[str, int] = {}
    for count, line in enumerate(lines, start=1):
        if count % YIELD_EVERY == 0:
            await asyncio.sleep(0)
        given = line.get(Column.HOST)
        host = normalize_host(given) if given else None
        if not given:
            skipped.append(_skipped(line, "в строке нет домена"))
        elif not host:
            skipped.append(_skipped(line, not_a_domain(given)))
        elif host in seen:
            skipped.append(_skipped(line, f"повтор: {host} уже в строке {seen[host]}", host=host))
        else:
            try:
                price = manual_price(
                    line.get(Column.PRICE),
                    line.get(Column.CURRENCY) or DEFAULT_CURRENCY,
                    line.get(Column.NOTE),
                    by=by,
                    grey=line.get(Column.GREY) or None,
                    payment=line.get(Column.PAYMENT) or None,
                )
            except ManualPriceError as exc:
                logger.debug("доноры списком: строка %s пропущена — %s", line.number, exc)
                skipped.append(_skipped(line, str(exc), host=host))
                continue
            seen[host] = line.number
            entries.append(_Entry(line, host, price))
    return entries, skipped


async def _known(
    session: AsyncSession, hosts: Sequence[str]
) -> tuple[dict[str, DomainModel], dict[str, DonorModel]]:
    """Домены и записи доноров этих доменов, что уже есть в базе, — двумя запросами."""
    if not hosts:
        return {}, {}
    domains = {
        domain.host: domain
        for domain in (
            await session.scalars(select(DomainModel).where(DomainModel.host.in_(list(hosts))))
        ).all()
    }
    by_id = {domain.id: host for host, domain in domains.items()}
    donors = {
        by_id[donor.domain_id]: donor
        for donor in (
            await session.scalars(select(DonorModel).where(DonorModel.domain_id.in_(list(by_id))))
        ).all()
    }
    return domains, donors


async def _write(
    session: AsyncSession, items: Sequence[_Write], *, by: str, author_id: int | None, at: datetime
) -> None:
    """Записать — тем же, что кнопка: принятие, цена в карточку, строка журнала о цене.

    Новые домены, новые доноры и журнал — вставками пачкой, а не объектом на строку: учёт
    единиц работы на тридцати тысячах объектов стоил секунды и держал цикл событий API.
    Принятие и цена новому донору ложатся теми же кусками формы (`manual_record.py`) на объект
    до вставки, и вставляются его колонки; прежние доноры правятся как у кнопки.
    """
    domains = await _domain_ids(session, items)
    born: list[DonorModel] = []
    changed: list[dict[str, Any]] = []
    befores: dict[str, str | None] = {}
    donor_ids: dict[int, int] = {}
    for count, item in enumerate(items, start=1):
        if count % YIELD_EVERY == 0:
            await asyncio.sleep(0)
        befores[item.entry.host] = None if item.donor is None else shown_price(item.donor)
        # Принятие и цена — на свой объект: новому донору это строка вставки, прежнему —
        # правка по номеру; объекты сессии не трогаются, и второй правки при фиксации нет.
        target = DonorModel(domain_id=domains[item.entry.host])
        if item.created:
            accept_by_hand(target, by=by, at=at)
        write_price(target, item.entry.price, at)
        if item.donor is None:
            item.donor = target
            born.append(target)
        else:
            donor_ids[item.donor.domain_id] = item.donor.id
            changed.append({**_columns(target), "id": item.donor.id})
    if changed:
        await session.execute(update(DonorModel), changed)
    if born:
        inserted = await session.execute(
            insert(DonorModel).returning(DonorModel.id, DonorModel.domain_id),
            [_columns(donor) for donor in born],
        )
        donor_ids.update({domain_id: donor_id for donor_id, domain_id in inserted.tuples()})
    await AccessRepository(session).record_many(
        AuditAction.PRICE_REVIEWED,
        [
            (
                author_id,
                f"donor:{donor_ids[domains[item.entry.host]]}",
                price_details(
                    item.entry.host,
                    item.entry.price,
                    created=item.created,
                    before=befores[item.entry.host],
                    via=VIA,
                ),
            )
            for item in items
        ],
    )


async def _domain_ids(session: AsyncSession, items: Sequence[_Write]) -> dict[str, int]:
    """Домен → номер: прежние как есть, новые — одной вставкой."""
    ids = {item.entry.host: item.domain.id for item in items if item.domain is not None}
    fresh = [{"host": item.entry.host} for item in items if item.domain is None]
    if fresh:
        inserted = await session.execute(
            insert(DomainModel).returning(DomainModel.id, DomainModel.host), fresh
        )
        ids.update({host: domain_id for domain_id, host in inserted.tuples()})
    return ids


def _columns(donor: DonorModel) -> dict[str, Any]:
    """Колонки, заданные у ещё не записанного донора, — для вставки пачкой; остальное —
    умолчаниями модели и базы, как у вставки объекта."""
    state = inspect(donor)
    return {
        attr.key: getattr(donor, attr.key)
        for attr in state.mapper.column_attrs
        if attr.key in state.dict
    }


def _entered(item: _Write) -> Row:
    """Строка записана (или будет записана в предпросмотре) — итогом и годностью к обходу."""
    crawlable = item.created or (item.donor is not None and suitable(item.donor))
    return Row(
        line=item.entry.line.number,
        given=item.entry.line.get(Column.HOST),
        outcome=Outcome.CREATED if item.created else Outcome.PRICED,
        host=item.entry.host,
        reason=None if crawlable else UNSUITABLE,
        amount=item.entry.price.amount,
        currency=item.entry.price.currency,
        crawlable=crawlable,
    )


def _skipped(line: Line, why: str, *, host: str | None = None) -> Row:
    return Row(
        line=line.number,
        given=line.get(Column.HOST),
        outcome=Outcome.SKIPPED,
        host=host,
        reason=why,
    )
