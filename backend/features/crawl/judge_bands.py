"""Полосы автоприменения советов судьи: включено ли, кто и когда — строками истории.

Слово владельца 10–11.11.2026: кнопка «Применить советы…» — ручной запуск, а разбирать спорных руками
некому — судья должен решать сам, каждую ночь (`judge_night.py`). **Что он решает сам, говорят
полосы**: «не пишем» и «пишем» — отдельно, обе по умолчанию выключены. Включает человек — после
показа плана полосы, выключает — одним нажатием.

**В базе, а не в окружении.** Править `.env` на бою некому, а выключатель должен быть под рукой у
того, кто видит очередь. Строка на каждую смену, действует последняя строка полосы, строк нет —
выключено: по строкам видно, кто и когда разрешил судье решать самому, — как у капа Ahrefs и
потолков модели (`core/llm_caps.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import JudgeBandModel
from backend.features.crawl.judge_apply import Band


@dataclass(frozen=True, slots=True)
class BandState:
    """Автоприменение полосы сейчас: включено ли и кто так поставил. Строк нет — выключено, и
    ставившего нет."""

    band: Band
    enabled: bool = False
    set_by: str | None = None
    set_at: datetime | None = None


async def states(session: AsyncSession) -> dict[Band, BandState]:
    """Обе полосы как они стоят сейчас — по последней строке каждой."""
    rows = await session.execute(
        select(JudgeBandModel.band, JudgeBandModel.enabled, UserModel.email, JudgeBandModel.set_at)
        .outerjoin(UserModel, UserModel.id == JudgeBandModel.set_by)
        .distinct(JudgeBandModel.band)
        .order_by(JudgeBandModel.band, JudgeBandModel.id.desc())
    )
    found = {band: BandState(band) for band in Band}
    for name, enabled, email, set_at in rows.tuples():
        # Полоса, которой код уже не знает (её сняли правкой), не полоса: строка остаётся историей.
        if name in Band:
            found[Band(name)] = BandState(Band(name), bool(enabled), email, set_at)
    return found


async def enabled(session: AsyncSession) -> frozenset[Band]:
    """Полосы, которые судья сейчас решает сам."""
    return frozenset(band for band, state in (await states(session)).items() if state.enabled)


async def set_band(session: AsyncSession, band: Band, *, on: bool, by: int | None) -> bool:
    """Включить или выключить автоприменение полосы — новой строкой истории. Ответ — как стояло
    до смены. Та же настройка второй раз строки не заводит: история — о сменах. Без фиксации: её
    делает вызывающий вместе с записью журнала."""
    was = (await states(session))[band].enabled
    if was != on:
        session.add(JudgeBandModel(band=band.value, enabled=on, set_by=by))
        await session.flush()
    return was
