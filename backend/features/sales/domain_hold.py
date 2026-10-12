"""Домен, который держат продажи — лид или кандидат сбора: одно правило для чисток аутрича.

Лид ссылается на домен без каскада (`sales_leads.domain_id` `RESTRICT`): строка домена
общая, и база не даст удалить её, пока на ней лид. Обе донорские чистки — основная
(`runs/prune.py`) и липовых доноров (`donors/probe.py`, `prune --probes`) — держат одно
правило: такой домен не удаляют и не падают на нём, а называют словами. Решать за продажи
они не вправе: пробу продаж целиком убирает только `prune --test-traces` (`sales/trials.py`).

**Кандидат сбора тоже держит домен** (`collecting`), пока он жив: не отсечён (полоса, а не
вердикт — у не судимого и у кандидата при выключенном судье вердикта нет) и лида из него нет.
Его ссылка — каскадом (`sales_segment_candidates.domain_id`): база удалить домен даст, и
чистка донорского прогона с тем же хостом молча снесла бы кандидата, его вердикт и найденный
адрес. Держит при любом этапе прогона: законченный оставляет «на проверке» человеку и принятых
без адреса, которых поиск повторит по сроку. Значит, домены базы сбора донорские чистки не
трогают никогда — отпускает их сам сбор: отсечённый кандидат домен не держит, а кандидата с
лидом держит лид.

**Проба — лид со своим ящиком** (`own_lead`, адрес из предохранителя отправки). Лид с чужим
адресом — не проба: его не убирает ни одна чистка, и слова плана говорят это прямо — удалить
такого лида может только человек, после этого домен уйдёт с `prune --probes`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel

Candidate = SalesSegmentCandidateModel


def collecting() -> ColumnElement[bool]:
    """Кандидат сбора, которому домен ещё нужен: не отсечён и лида из него нет."""
    return and_(Candidate.lead_id.is_(None), Candidate.lane != Lane.REJECT)


def holds_domain() -> ColumnElement[bool]:
    """Продажи держат домен — лид или живой кандидат сбора — условием запроса по `DomainModel`."""
    return or_(
        exists().where(SalesLeadModel.domain_id == DomainModel.id),
        exists().where(Candidate.domain_id == DomainModel.id, collecting()),
    )


def own_lead(addresses: Collection[str]) -> ColumnElement[bool]:
    """Лид пробы — со своим ящиком: адрес из предохранителя (`OwnInboxes.addresses`), без учёта
    регистра и пробелов."""
    return func.lower(func.trim(SalesLeadModel.email)).in_(tuple(addresses))


def _numbers(lead_ids: Sequence[int]) -> str:
    return ", ".join(f"№{number}" for number in lead_ids)


def kept_words(trials: Sequence[int], foreign: Sequence[int] = (), runs: Sequence[int] = ()) -> str:
    """Почему домен оставлен — словами плана чистки: пробы продаж (`trials`) убирает
    `prune --test-traces`, лидов с чужим адресом (`foreign`) — только человек, кандидата
    сбора (`runs` — его прогоны) отпускает сам сбор."""
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
    if len(runs) == 1:
        said.append(
            f"держит кандидат сбора продаж (прогон {_numbers(runs)}) — отпустит сам сбор, "
            "когда кандидата отсекут или из него выйдет лид"
        )
    elif runs:
        said.append(
            f"держат кандидаты сбора продаж (прогоны {_numbers(runs)}) — отпустит сам сбор, "
            "когда их отсекут или из них выйдут лиды"
        )
    return "домен " + "; ".join(said)


async def held(session: AsyncSession, domains: ColumnElement[bool]) -> dict[str, str]:
    """Домены под условием, которые держат продажи: домен → почему оставлен. Ничего не меняет."""
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
    collected = await session.execute(
        select(DomainModel.host, Candidate.run_id)
        .distinct()
        .join(Candidate, Candidate.domain_id == DomainModel.id)
        .where(domains, collecting())
        .order_by(DomainModel.host, Candidate.run_id)
    )
    runs: defaultdict[str, list[int]] = defaultdict(list)
    for host, run_id in collected.tuples():
        runs[host].append(run_id)
    hosts = sorted({*trials, *foreign, *runs})
    return {host: kept_words(trials[host], foreign[host], runs[host]) for host in hosts}
