"""Доли стран у сохранённых доноров — заново, по их же числам, без провайдера.

07.10.2026 знаменатель доли сменился (`geo.share_base`): трафик домена и трафик
по странам приходят разными запросами и расходятся, и донор стоял на экране
с «Канада · 128%». Новые прогоны пишут доли по-новому, а записанные раньше
остались бы прежними до следующей покупки метрик — через полгода.

**Пересчёт не покупает ничего.** В разбивке (`donors.geo_breakdown`) лежит
трафик каждой страны, рядом — трафик домена (`donors.org_traffic`), то есть
ровно то, из чего доля и считалась. Считает та же функция, что и прогон
(`geo.build_breakdown`), — второй копии правила нет.

**Решение по региону не меняется.** Место страны в топе считается по трафику,
а не по доле; доля нужна правилу только за пределами топа, а в ответе
с пятью строками стран таких нет. Поэтому статус и причина отказа не трогаются.

Повторный пересчёт ничего не меняет: пересчитанное совпадает с записанным.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.geo import build_breakdown


@dataclass(frozen=True, slots=True)
class ShareFix:
    """Донор, у которого доли изменятся: верхняя доля до и после и новая разбивка."""

    donor_id: int
    host: str
    before: float | None
    after: float
    breakdown: list[dict[str, Any]]


@dataclass(slots=True)
class RecountPlan:
    """Что изменится. `unreadable` — разбивка есть, а пересчитать её нечем:
    нет трафика домена или строка без страны и трафика. Такие не трогаются
    и называются — молча пропущенный донор выглядел бы исправленным."""

    checked: int = 0
    fixes: list[ShareFix] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


#: Строка выборки: номер донора, хост, трафик домена, разбивка, верхняя доля.
type _Row = tuple[int, str, int | None, Any, float | None]


async def plan_recount(session: AsyncSession) -> RecountPlan:
    """Кому доли пересчитаются. Только читает."""
    rows = await session.execute(
        select(
            DonorModel.id,
            DomainModel.host,
            DonorModel.org_traffic,
            DonorModel.geo_breakdown,
            DonorModel.geo_top_share,
        )
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        # Пустая разбивка хранится JSON-значением null, а не пустотой столбца:
        # `IS NOT NULL` её не отсёк бы, и в выборку шёл бы каждый отсеянный по DR.
        .where(func.jsonb_typeof(DonorModel.geo_breakdown) == "array")
        .order_by(DonorModel.id)
    )
    return _plan(rows.tuples().all())


def _plan(rows: Sequence[_Row]) -> RecountPlan:
    plan = RecountPlan(checked=len(rows))
    for donor_id, host, total, stored, top_share in rows:
        fresh = _recounted(stored, total)
        if fresh is None:
            plan.unreadable.append(host)
        elif not _same(stored, top_share, fresh):
            plan.fixes.append(ShareFix(donor_id, host, top_share, fresh[0]["share"], fresh))
    return plan


def _recounted(stored: list[Any], total: int | None) -> list[dict[str, Any]] | None:
    """Разбивка по нынешнему правилу. `None` — пересчитать нечем."""
    if total is None or total <= 0 or not stored or not all(map(_readable, stored)):
        return None
    return [asdict(item) for item in build_breakdown(stored, total)]


def _readable(row: Any) -> bool:
    """Строка, из которой доля считается: страна и число трафика."""
    if not isinstance(row, dict):
        return False
    traffic = row.get("org_traffic")
    return isinstance(row.get("country"), str) and isinstance(traffic, int | float)


def _same(stored: list[dict[str, Any]], top: float | None, fresh: list[dict[str, Any]]) -> bool:
    """Записанное уже посчитано нынешним правилом — трогать нечего."""
    if top is None or not math.isclose(top, fresh[0]["share"]):
        return False
    return all(
        isinstance(old.get("share"), int | float) and math.isclose(old["share"], new["share"])
        for old, new in zip(stored, fresh, strict=True)
    )


async def apply_recount(session: AsyncSession, plan: RecountPlan) -> int:
    """Записать пересчитанное. Возвращает, скольким донорам записано.

    Без коммита: решает вызывающий. Запись — только если верхняя доля всё ещё
    та, что видел план: прогон, обновивший донора между планом и записью,
    уже посчитал доли сам, и затирать их старыми числами нельзя.
    """
    written = 0
    for fix in plan.fixes:
        updated = await session.execute(
            update(DonorModel)
            .where(
                DonorModel.id == fix.donor_id,
                DonorModel.geo_top_share.is_not_distinct_from(fix.before),
            )
            .values(geo_breakdown=fix.breakdown, geo_top_share=fix.after)
            .returning(DonorModel.id)
        )
        if updated.scalar_one_or_none() is not None:
            written += 1
    return written
