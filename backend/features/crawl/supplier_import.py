"""Список поставщиков консолью — в стоп-лист экрана (решение 12.10.2026).

`outreach suppliers-import` вносит каждого поставщика записью стоп-листа с причиной
«поставщик» — той же, что заводит экран «Стоп-лист» (`letters/stoplist.add`). Поставщик
виден на экране и снимается там же, держит письма ему самому и отсеивается на обоих
этапах: запись без этапа читают и отбор прогона Этапа 1 (`runs/exclusions.py`), и
правило Этапа 2 (`suppliers.supplier_hosts`).

До решения консоль писала отдельную таблицу `supplier_donors`: экран её не показывал,
а письма самому поставщику она не держала. Прежние записи читаются как раньше.

**Срок.** Список размещений — `ADVERTISERS_SUPPLIER_TERM_MONTHS` (12 месяцев),
партнёры — `--forever`, без срока. Действующую запись повторный импорт продлевает,
но не укорачивает: партнёр, попавший и в список размещений, остаётся бессрочным.
Истёкшая не продлевается, а остаётся историей — рядом заводится новая, как на экране.

**Домен уже в стоп-листе по другой причине** (отписка, жалоба, ручная запись): вторую
запись на тот же адрес стоп-лист не заводит. Поставщик всё равно держится на свой срок
(ревью 12.10.2026): срок той записи продлевается до срока поставщика, если он позже, —
и истечение её прежнего срока поставщику писем не откроет; отписка и жалоба и так
бессрочны. Поставщиком домен отмечается в прежнем списке консоли — его читают правило
поставщиков и отбор прогона, — а причина «поставщик» и новый срок пишутся в журнал
рядом с той записью. Импорт называет такие домены словами.

**Чьи письма сняты.** Запись снимает с очереди и сроков добивок все письма адресату,
как у экрана: и переписку Этапа 1, и оффер, и продажи. Импорт называет этапы числами.

**Почему в списке** («размещались в марте», «партнёр») — в журнал, рядом с автором:
у записи стоп-листа нет поля причины словами.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus, SuppressionReason
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.crawl.suppliers import term_end
from backend.features.letters import stoplist

logger = logging.getLogger(__name__)

#: Откуда запись — для журнала: экран пишет своё, консоль — своё.
SOURCE = "outreach suppliers-import"


@dataclass(slots=True)
class SupplierImport:
    """Чем кончился импорт списка — числами и словами."""

    added: int = 0
    renewed: int = 0
    kept: int = 0
    #: Писем снято новыми записями — из очереди и со сроков добивок, как у экрана.
    stopped: int = 0
    #: Домены, которых база не знала: запись завела их сама. Опечатка выглядит так же.
    new_domains: list[str] = field(default_factory=list)
    #: Писем снято по этапам: этап → сколько. Запись снимает письма всех этапов.
    stopped_by_stage: Counter[str] = field(default_factory=Counter)
    #: Уже в стоп-листе по другой причине: домен → причина. Отмечены в прежнем списке.
    elsewhere: dict[str, str] = field(default_factory=dict)
    #: Из них — чья запись продлена до срока поставщика: домен → новый срок словами.
    extended: dict[str, str] = field(default_factory=dict)
    #: Строки файла, в которых нет домена: их называют, а не теряют молча.
    rejected: list[str] = field(default_factory=list)


def parse_line(raw: str) -> tuple[str, str | None] | None:
    """Строка файла: домен, причина после решётки. `None` — пустая или комментарий.

    Домен — тем ключом, что у экрана (`stoplist.site_of`): ссылка, `www.` и поддомен
    сводятся к корню сайта. Не домен — пустой ключ, и строку называют отказом.
    """
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    entered, _, note = line.partition("#")
    try:
        host = stoplist.site_of(entered.strip().lower())
    except stoplist.StopListError:
        # Не домен — строка уходит в отказы импорта и называется человеку там.
        logger.debug("Строка файла поставщиков без домена: %.80r", entered)
        host = ""
    return host, note.strip() or None


@dataclass(frozen=True, slots=True)
class _Entry:
    """Одна запись импорта и всё, что о ней пишется."""

    host: str
    note: str | None
    until: datetime | None
    by: str


async def import_hosts(
    session: AsyncSession,
    lines: Sequence[str],
    *,
    by: str,
    forever: bool = False,
    now: datetime | None = None,
) -> SupplierImport:
    """Внести список поставщиков в стоп-лист. `forever` — текущие партнёры, без срока.

    Без фиксации — её делает вызывающий: запись, снятые письма и журнал — одна транзакция.
    """
    moment = now or datetime.now(UTC)
    until = None if forever else term_end(moment)
    report = SupplierImport()
    notes: dict[str, str | None] = {}
    for raw in lines:
        found = parse_line(raw)
        if found is None:
            continue
        host, note = found
        if not host:
            report.rejected.append(raw.strip())
            continue
        notes.setdefault(host, note)
    for host, note in notes.items():
        await _one(session, _Entry(host, note, until, by), moment, report)
    await session.flush()
    return report


async def _one(
    session: AsyncSession, entry: _Entry, moment: datetime, report: SupplierImport
) -> None:
    """Один поставщик: новая запись, продление, «уже было» или отметка в прежнем списке."""
    held = await _held(session, entry.host, moment)
    if held is None:
        report.stopped_by_stage.update(await _pending_by_stage(session, entry.host))
        added = await stoplist.add(
            session,
            entry.host,
            reason=SuppressionReason.SUPPLIER,
            expires_at=entry.until,
            author=entry.by,
        )
        report.added += 1
        report.stopped += added.stopped
        if added.new_domain:
            report.new_domains.append(entry.host)
        await _journal(session, added.id, entry, "внесён")
        return
    if held.reason is not SuppressionReason.SUPPLIER:
        report.elsewhere[entry.host] = held.reason.value
        await _mark_listed(session, entry)
        if _later(entry.until, held.expires_at):
            held.expires_at = entry.until
            report.extended[entry.host] = _until_words(entry.until)
        what = f"поставщик; запись «{held.reason.value}» держит до {_until_words(held.expires_at)}"
        await _journal(session, held.id, entry, what)
        return
    if not _later(entry.until, held.expires_at):
        report.kept += 1
        return
    held.expires_at = entry.until
    report.renewed += 1
    await _journal(session, held.id, entry, "срок продлён")


async def _held(session: AsyncSession, host: str, moment: datetime) -> SuppressionModel | None:
    """Действующая запись стоп-листа на домен целиком, на всех этапах, — какая есть."""
    found: SuppressionModel | None = await session.scalar(
        select(SuppressionModel)
        .join(DomainModel, DomainModel.id == SuppressionModel.domain_id)
        .where(
            DomainModel.host == host,
            SuppressionModel.stage.is_(None),
            SuppressionModel.in_force(moment),
        )
        .order_by(SuppressionModel.id)
        .limit(1)
    )
    return found


async def _mark_listed(session: AsyncSession, entry: _Entry) -> None:
    """Отметить поставщиком в прежнем списке консоли — домен уже в стоп-листе."""
    row = await session.scalar(
        select(SupplierDonorModel).where(SupplierDonorModel.host == entry.host)
    )
    if row is None:
        session.add(
            SupplierDonorModel(
                host=entry.host, note=entry.note, added_by=entry.by, expires_at=entry.until
            )
        )
    elif _later(entry.until, row.expires_at):
        row.expires_at = entry.until


async def _journal(session: AsyncSession, row_id: int, entry: _Entry, what: str) -> None:
    """Запись — в журнал, как у экрана: кто, до какого срока и почему в списке."""
    await AccessRepository(session).record(
        AuditAction.SUPPRESSION_ADDED,
        target=f"suppression:{row_id}",
        details={
            "кому не пишем": entry.host,
            "причина": SuppressionReason.SUPPLIER.value,
            "до": entry.until.isoformat() if entry.until else "навсегда",
            "почему в списке": entry.note,
            "кто внёс": entry.by,
            "откуда": SOURCE,
            "что": what,
        },
    )


async def _pending_by_stage(session: AsyncSession, host: str) -> dict[str, int]:
    """Что снимет запись на домен: письма в очереди и со сроком добивки — по этапам.
    Условие то же, что у снятия (`stoplist.stop_pending` по домену)."""
    rows = await session.execute(
        select(CampaignModel.stage, func.count(MessageModel.id))
        .join(MessageModel, MessageModel.campaign_id == CampaignModel.id)
        .join(DomainModel, DomainModel.id == MessageModel.domain_id)
        .where(
            DomainModel.host == host,
            or_(
                MessageModel.status == MessageStatus.QUEUED,
                MessageModel.next_action_at.is_not(None),
            ),
        )
        .group_by(CampaignModel.stage)
    )
    return {stage.value: int(count) for stage, count in rows.all()}


def _until_words(until: datetime | None) -> str:
    return "навсегда" if until is None else f"{until:%d.%m.%Y}"


def _later(new: datetime | None, old: datetime | None) -> bool:
    """Новый срок позже прежнего. Пусто — бессрочно, то есть позже любого."""
    if old is None:
        return False
    return new is None or new > old
