"""«Судья сам, каждую ночь»: как стоят полосы автоприменения, итог последней ночи и переключатель.

Ночной шаг (`crawl/judge_night.py`) решает спорных сам только по включённым полосам. Здесь — то, что
видит и чем управляет человек.

**Права.** Смотреть — под `view`: полосы, итог ночи и «ждут перевода» — числа и слова. Включать и
выключать полосу — под теми же двумя правами, что кнопка «Применить советы…» (`run` и `prices`
разом, `judge_apply.applier`): включённая полоса — та же кнопка, нажатая заранее на каждую ночь.

**Смена полосы — в журнал действий** («было → стало», кто): с этой минуты судья решает сам, и по
журналу видно, кто это разрешил и кто остановил. Та же настройка второй раз записи не заводит.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.advertisers.judge_apply import applier
from backend.api.deps import db_session, needs
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import judge_bands, judge_night_view
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_night_view import NightSummary, NightView

router = APIRouter(prefix="/judge-night")

_viewer = Depends(needs(Permission.VIEW))

#: Полоса в журнале действий — словами.
SWITCH = {True: "включено", False: "выключено"}


class BandStateView(BaseModel):
    """Автоприменение полосы: включено ли и кто так поставил. Не ставили ни разу — выключено,
    `set_by` и `set_at` пусты."""

    band: Band
    enabled: bool
    set_by: str | None
    set_at: datetime | None


class NightSummaryView(BaseModel):
    """Итог последней ночи. `judged` — пар разобрано, `applied` — решено самой ночью по полосам
    (`skip`, `write`; полосы нет — она была выключена), `unclear` — «не понять» среди разобранных.
    `going` — шаг ещё идёт и что делает; `why` — почему не дошёл до конца или разбор встал.
    `lines` — строки для «i»: итог разбора как есть, пересчёт советов, полосы."""

    night: date
    status: str
    judged: int
    applied: dict[str, int]
    unclear: int
    going: str | None
    why: str | None
    lines: list[str]

    @classmethod
    def of(cls, summary: NightSummary) -> NightSummaryView:
        return cls(**asdict(summary))


class NightAutoView(BaseModel):
    """Автоприменение на экране. `ran_today` — шаг этой даты уже начинался: полоса, включённая
    сейчас, пойдёт следующей ночью. `awaiting_promotion` — доменов ждут «Перевести»: ночь этого
    не делает."""

    bands: list[BandStateView]
    last: NightSummaryView | None
    ran_today: bool
    awaiting_promotion: int

    @classmethod
    def of(cls, found: NightView) -> NightAutoView:
        return cls(
            bands=[BandStateView(**asdict(state)) for state in found.bands.values()],
            last=None if found.last is None else NightSummaryView.of(found.last),
            ran_today=found.ran_today,
            awaiting_promotion=found.awaiting_promotion,
        )


class BandBody(BaseModel):
    """Включить или выключить автоприменение полосы. Лишнее поле — отказ."""

    model_config = ConfigDict(extra="forbid")

    band: Band
    enabled: bool


async def _view(session: AsyncSession) -> NightAutoView:
    return NightAutoView.of(await judge_night_view.view(session, now=datetime.now(UTC)))


@router.get("", response_model=NightAutoView, summary="Судья сам: полосы и итог последней ночи")
async def read_night(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> NightAutoView:
    """Как стоят полосы, что судья сделал последней ночью и сколько доменов ждут перевода."""
    return await _view(session)


@router.post("/bands", response_model=NightAutoView, summary="Включить или выключить полосу")
async def set_band(
    body: BandBody,
    author: UserModel = Depends(applier),
    session: AsyncSession = Depends(db_session),
) -> NightAutoView:
    """Автоприменение полосы — новой строкой истории и записью журнала; то же, что стоит, — без
    записи."""
    was = await judge_bands.set_band(session, body.band, on=body.enabled, by=author.id)
    if was != body.enabled:
        await AccessRepository(session).record(
            AuditAction.ADVERTISER_JUDGE_BANDS_CHANGED,
            author_id=author.id,
            target="advertisers:judge-night",
            details={
                "полоса": body.band.value,
                "автоприменение": {"было": SWITCH[was], "стало": SWITCH[body.enabled]},
            },
        )
    await session.commit()
    return await _view(session)
