"""Что база знает о партии лидов очистки — одним чтением на партию, а не на лида.

Правила отказа очистки (`cleaning._RULES`) и стоп-лист продаж (`stoplist_leads`) судят лида по
тому, что о партии уже лежит в базе: первый лид с тем же адресом (оригинал дубля), домен компании,
записи стоп-листа продаж, отписки общего стоп-листа, которые держат продажи
(`suppressions.holds_sales`), и сайты в работе у другого направления (`elsewhere.roles`).

Своим модулем, а не в `cleaning.py`: очистка стоит у предела длины файла, а чтение партии —
отдельная забота, и у стоп-листа она та же.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.sales import elsewhere
from backend.features.sales.models import SalesLeadModel, SalesStoplistModel
from backend.features.sales.suppressions import holds_sales

#: Значений в одном `IN`: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000


@dataclass(frozen=True, slots=True)
class Known:
    """Что база знает о партии лидов — одним чтением на партию, а не на лида."""

    #: Адрес → наименьший id лида с ним: он и есть оригинал.
    first_lead: dict[str, int]
    hosts: dict[int, str]
    stop_hosts: frozenset[str]
    stop_emails: frozenset[str]
    unsub_emails: frozenset[str]
    unsub_domains: frozenset[int]
    #: Домен → направление, у которого сайт в работе (`elsewhere.roles`).
    busy_domains: dict[int, Stage]


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _unsub(column: Any, chunk: Sequence[Any], moment: datetime) -> Any:
    """Строки общего стоп-листа, которые держат продажи (`suppressions.holds_sales`)."""
    return select(column).where(holds_sales(moment), column.in_(chunk))


async def load_known(
    session: AsyncSession, leads: Sequence[SalesLeadModel], moment: datetime
) -> Known:
    emails = sorted({lead.email for lead in leads})
    domain_ids = sorted({lead.domain_id for lead in leads})
    first: dict[str, int] = {}
    hosts: dict[int, str] = {}
    unsub_emails: set[str] = set()
    unsub_domains: set[int] = set()
    busy: dict[int, Stage] = {}
    for chunk in _chunks(emails):
        same = select(SalesLeadModel.email, func.min(SalesLeadModel.id))
        rows = await session.execute(
            same.where(SalesLeadModel.email.in_(chunk)).group_by(SalesLeadModel.email)
        )
        first.update(dict(rows.all()))  # type: ignore[arg-type]
        unsub_emails.update(
            (await session.scalars(_unsub(SuppressionModel.email, chunk, moment))).all()
        )
    for chunk in _chunks(domain_ids):
        named = select(DomainModel.id, DomainModel.host).where(DomainModel.id.in_(chunk))
        hosts.update(dict((await session.execute(named)).all()))  # type: ignore[arg-type]
        unsub_domains.update(
            (await session.scalars(_unsub(SuppressionModel.domain_id, chunk, moment))).all()
        )
        busy.update(await elsewhere.roles(session, chunk))
    stop = (await session.execute(select(SalesStoplistModel.host, SalesStoplistModel.email))).all()
    return Known(
        first_lead=first,
        hosts=hosts,
        stop_hosts=frozenset(host for host, _ in stop if host),
        stop_emails=frozenset(email for _, email in stop if email),
        unsub_emails=frozenset(unsub_emails),
        unsub_domains=frozenset(unsub_domains),
        busy_domains=busy,
    )
