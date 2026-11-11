"""Партии советов судьи в журнале действий: что пишется о порции и откате и как из этого читается
список партий.

**Своей таблицы у партий нет.** Партия — это метка автора у решений (`judge_mark.py`) и записи
журнала о ней: каждая применённая порция и каждый откат — своей записью, той же транзакцией, что
решения. Запись журнала не правится (`AuditLogModel`), поэтому итог партии — сумма её порций, а не
одна строка, которую дописывают: обрыв посреди партии оставляет в журнале ровно то, что применено.

**Список партий — из тех же записей**: кто начал и когда — первая порция, сколько применено — сумма
порций, откатана ли — есть ли запись отката. Рядом — сколько решений партии в силе сейчас, счётом
по метке: человек мог переиграть часть, а новый обход — не найти пару.

Ключи подробностей — словами, как у остальных записей журнала: экран показывает их как есть.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.judge_apply import ANSWERED_NAMED, Applied
from backend.features.crawl.judge_mark import BATCH, PREFIX, by_judge, started_at
from backend.features.crawl.judge_revert import Reverted

#: Вид цели записи журнала: `judge_batch:<партия>`.
TARGET = "judge_batch"
#: Сколько последних партий показывает экран и сколько записей журнала ради них читается: порций у
#: партии — единицы.
BATCHES = 10
MAX_BATCHES = 50
SCANNED = 400
#: Запас на расхождение часов: имя партии даёт сервер приложения, время записи журнала — база.
CLOCK_SLACK = timedelta(days=1)

BAND_KEY = "полоса"
APPLIED_KEY = "применено"
LEFT_KEY = "осталось"
RETURNED_KEY = "возвращено в очередь"
#: Кто, когда записи пишет не человек: имя автоматики (журнал хранит автора номером учётки).
WHO_KEY = "кто"


def batch_target(batch: str) -> str:
    return f"{TARGET}:{batch}"


def portion_details(result: Applied) -> dict[str, Any]:
    """Порция партии — подробностями журнала: нули не пишутся, кроме «применено» и «осталось»."""
    details: dict[str, Any] = {
        BAND_KEY: result.band.value,
        APPLIED_KEY: result.applied,
        LEFT_KEY: result.left,
    }
    extra = {
        "уже решены": result.decided,
        "строку заменил обход": result.gone,
        "рекламодателей снято": result.removed,
        "снято писем": result.stopped,
        "уже ответили": len(result.answered),
    }
    details.update({key: value for key, value in extra.items() if value})
    if result.answered:
        details["переписка идёт"] = result.answered[:ANSWERED_NAMED]
    return details


def revert_details(result: Reverted, band: str) -> dict[str, Any]:
    """Откат партии — подробностями журнала."""
    details: dict[str, Any] = {BAND_KEY: band, RETURNED_KEY: result.returned}
    extra = {
        "письмо ушло — решение осталось": result.kept,
        "снято из памяти обходов": result.forgotten,
        "рекламодателей снято": result.removed,
        "снято писем": result.stopped,
    }
    details.update({key: value for key, value in extra.items() if value})
    return details


@dataclass(slots=True)
class Batch:
    """Партия в списке: кто, когда, какой полосой, сколько и откатана ли."""

    id: str
    band: str
    by: str | None
    started_at: datetime
    applied: int = 0
    #: Сколько пар полосы оставалось после последней порции: не ноль — партию оборвали.
    left: int = 0
    #: Решений партии в силе сейчас.
    standing: int = 0
    reverted_at: datetime | None = None
    reverted_by: str | None = None
    returned: int = 0


def _number(details: dict[str, Any], key: str) -> int:
    value = details.get(key)
    return value if isinstance(value, int) else 0


def _who(details: dict[str, Any], email: str | None) -> str | None:
    said = details.get(WHO_KEY)
    return email or (said if isinstance(said, str) else None)


def _batch_of(target: str | None) -> str | None:
    """Партия из цели записи; цель не партии — `None`."""
    name = (target or "").removeprefix(f"{TARGET}:")
    return name if BATCH.fullmatch(name) else None


def _fold(rows: Sequence[tuple[AuditLogModel, str | None]]) -> dict[str, Batch]:
    """Записи журнала от старых к новым → партии: первая порция называет автора и начало."""
    found: dict[str, Batch] = {}
    for entry, email in rows:
        name, details = _batch_of(entry.target), entry.details or {}
        if name is None:
            continue
        who = _who(details, email)
        if entry.action is AuditAction.ADVERTISER_JUDGE_APPLIED:
            band = str(details.get(BAND_KEY, name.split("-", 1)[0]))
            batch = found.setdefault(name, Batch(name, band, who, entry.created_at))
            batch.applied += _number(details, APPLIED_KEY)
            batch.left = _number(details, LEFT_KEY)
        elif name in found:
            batch = found[name]
            batch.reverted_at, batch.reverted_by = entry.created_at, who
            batch.returned += _number(details, RETURNED_KEY)
    return found


async def _entries(
    session: AsyncSession, *, batch: str | None = None, limit: int = SCANNED
) -> list[tuple[AuditLogModel, str | None]]:
    """Последние записи о партиях (или об одной) — от старых к новым."""
    query = (
        select(AuditLogModel, UserModel.email)
        .outerjoin(UserModel, UserModel.id == AuditLogModel.user_id)
        .where(
            AuditLogModel.action.in_(
                (AuditAction.ADVERTISER_JUDGE_APPLIED, AuditAction.ADVERTISER_JUDGE_REVERTED)
            )
        )
        .order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
        .limit(limit)
    )
    if batch is not None:
        query = query.where(AuditLogModel.target == batch_target(batch))
        # Об одной партии спрашивает каждая порция: записи о ней не старше её начала, и журнал
        # читается с этого места по индексу времени, а не целиком. Имя уже проверено
        # (`judge_apply.band_of`): время из него читается.
        query = query.where(AuditLogModel.created_at >= started_at(batch) - CLOCK_SLACK)
    rows = await session.execute(query)
    return list(reversed(rows.tuples().all()))


async def _standing(session: AsyncSession) -> dict[str, int]:
    """Сколько решений каждой партии в силе сейчас — счётом по метке автора."""
    counted = await session.execute(
        select(CandidateModel.decided_by, func.count())
        .where(by_judge())
        .group_by(CandidateModel.decided_by)
    )
    return {str(by).removeprefix(PREFIX): int(number) for by, number in counted.tuples()}


async def batches(session: AsyncSession, *, limit: int = BATCHES) -> list[Batch]:
    """Последние партии, новые сверху."""
    found = _fold(await _entries(session))
    standing = await _standing(session)
    for name, batch in found.items():
        batch.standing = standing.get(name, 0)
    return sorted(found.values(), key=lambda one: one.started_at, reverse=True)[:limit]


async def batch(session: AsyncSession, name: str) -> Batch | None:
    """Партия по имени; в журнале её нет — `None`."""
    return _fold(await _entries(session, batch=name)).get(name)
