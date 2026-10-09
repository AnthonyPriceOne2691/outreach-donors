"""Домен, который держит лид продаж: одно правило для чисток аутрича.

Лид ссылается на домен без каскада (`sales_leads.domain_id` `RESTRICT`): строка домена
общая, и база не даст удалить её, пока на ней лид. Обе донорские чистки — основная
(`runs/prune.py`) и липовых доноров (`donors/probe.py`, `prune --probes`) — держат одно
правило: такой домен не удаляют и не падают на нём, а называют словами. Решать за продажи
они не вправе: пробу продаж целиком убирает только `prune --test-traces` (`sales/trials.py`).

**Проба — лид со своим ящиком** (`own_lead`, адрес из предохранителя отправки). Лид с чужим
адресом — не проба: его не убирает ни одна чистка, и слова плана говорят это прямо — удалить
такого лида может только человек, после этого домен уйдёт с `prune --probes`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, Exists, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import SalesLeadModel


def holds_domain() -> Exists:
    """Лид продаж на домене — условием запроса по `DomainModel`."""
    return exists().where(SalesLeadModel.domain_id == DomainModel.id)


def own_lead(addresses: Collection[str]) -> ColumnElement[bool]:
    """Лид пробы — со своим ящиком: адрес из предохранителя (`OwnInboxes.addresses`), без учёта
    регистра и пробелов."""
    return func.lower(func.trim(SalesLeadModel.email)).in_(tuple(addresses))


def _numbers(lead_ids: Sequence[int]) -> str:
    return ", ".join(f"№{number}" for number in lead_ids)


def kept_words(trials: Sequence[int], foreign: Sequence[int] = ()) -> str:
    """Почему домен оставлен — словами плана чистки: пробы продаж (`trials`) убирает
    `prune --test-traces`, лидов с чужим адресом (`foreign`) — только человек."""
    said: list[str] = []
    if len(trials) == 1:
        said.append(f"держит лид продаж {_numbers(trials)} — его убирает `prune --test-traces`")
    elif trials:
        said.append(f"держат лиды продаж {_numbers(trials)} — их убирает `prune --test-traces`")
    if len(foreign) == 1:
        said.append(
            f"держит лид продаж {_numbers(foreign)} с чужим адресом — не проба своего ящика, "
            "`prune --test-traces` его не уберёт: удалить лида может только человек, тогда "
            "домен уйдёт с `prune --probes`"
        )
    elif foreign:
        said.append(
            f"держат лиды продаж {_numbers(foreign)} с чужими адресами — не пробы своего ящика, "
            "`prune --test-traces` их не уберёт: удалить лидов может только человек, тогда "
            "домен уйдёт с `prune --probes`"
        )
    return "домен " + "; ".join(said)


async def held(session: AsyncSession, domains: ColumnElement[bool]) -> dict[str, str]:
    """Домены под условием, которые держит лид продаж: домен → почему оставлен. Ничего не меняет."""
    # Свои ящики — при вызове: `outreach/own_inboxes.py` при загрузке сам грузит чистку липовых
    # доноров (`donors/probe.py`), а она — этот модуль.
    from backend.features.outreach.own_inboxes import own_inboxes  # noqa: PLC0415 — круг импорта

    rows = await session.execute(
        select(DomainModel.host, SalesLeadModel.id, own_lead(own_inboxes().addresses))
        .join(SalesLeadModel, SalesLeadModel.domain_id == DomainModel.id)
        .where(domains)
        .order_by(DomainModel.host, SalesLeadModel.id)
    )
    trials: defaultdict[str, list[int]] = defaultdict(list)
    foreign: defaultdict[str, list[int]] = defaultdict(list)
    for host, lead_id, own in rows.tuples():
        (trials if own else foreign)[host].append(lead_id)
    return {host: kept_words(trials[host], foreign[host]) for host in sorted({*trials, *foreign})}
