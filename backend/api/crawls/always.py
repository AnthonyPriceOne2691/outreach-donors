"""Обход 24/7 на экране: пуск и пауза, порог «мало рекламодателей», переобход слабых и пометка
донора руками.

Обход 24/7 (`crawl/always.py`) ставит части сам — только когда его запустили. Здесь — то, что видит
и чем управляет человек на вкладке «Обход доноров».

**Права.** Смотреть — под `view`: это числа и слова. Запускать, ставить на паузу, менять порог и
пометку — под `run`: это «запустить работу, которая идёт сама», как кнопки обхода. Весь роутер — ещё
и под правом этапа (`stage_access.STAGE_TWO`), как остальные маршруты обходов.

**Смена — в журнал действий** («было → стало», кто): с этой минуты обходчики идут иначе, и по
журналу видно, кто так велел. Та же настройка второй раз записи не заводит.

**Рубильник сервиса выключен** (`config/crawl_always`) — кнопок нет: экран говорит об этом словами,
пуск и смена — отказом словами. Пометку донора можно ставить и тогда: это оценка сайта, а не пуск.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.crawls.schemas import DonorDepthView
from backend.api.deps import db_session, needs
from backend.config import crawl_always as cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import always_depth, always_switch, always_view
from backend.features.crawl.always_switch import Switch, threshold_problem
from backend.features.crawl.always_view import View
from backend.features.crawl.launch import crawl_host

router = APIRouter(prefix="/always")

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

#: Цель записи журнала о выключателе.
TARGET = "crawls:always"
#: Выключатель и переобход в журнале действий — словами.
STATE = {True: "идёт", False: "на паузе"}
RECRAWL = {True: "включён", False: "выключен"}
#: Пометка донора в журнале — словами.
MARK = {True: "помечен руками", False: "пометка снята руками", None: "решает порог"}
#: Рубильник сервиса выключен — смена отказом.
SWITCHED_OFF = (
    "Обход 24/7 выключен настройкой сервиса: кнопок на экране нет, пока его не включат на "
    "сервере. Обходы — кнопками и ночным обходом, как до него"
)
#: Пометка — донору: кого нет среди доноров, того и не пометить.
NO_DONOR = "Такого донора нет: пометку «мало рекламодателей» ставят донору из базы"


class AlwaysCounts(BaseModel):
    """Сайты обхода 24/7 по очередям: `new` — ни разу не обходились, `first` — не прошли первые
    10 000, `deeper` — идут дальше, `weak` — пропускаются как «мало рекламодателей», `exhausted` —
    пройдены до конца; `running` — частей идёт сейчас, `total` — сайтов в обходе."""

    new: int
    first: int
    deeper: int
    weak: int
    exhausted: int
    running: int
    total: int


class AlwaysView(BaseModel):
    """Обход 24/7 на экране. `available` — рубильник сервиса включён: без него кнопок нет.
    `waiting_for` — почему включённый сейчас не идёт; `words` — строка блока, `details` — строки
    «i». `pages` — страниц, пройденных обходом 24/7, `part_pages` — страниц в части после первой.
    `yields` — доли продающих страниц, %, у сайтов, чью пометку решает порог: по ним экран
    считает, сколько сайтов пометит порог. `done_at` — когда сказано «пройдено всё»."""

    available: bool
    enabled: bool
    set_at: datetime | None
    set_by: str | None
    threshold_pct: float
    recrawl_weak: bool
    waiting_for: str | None
    counts: AlwaysCounts
    pages: int
    part_pages: int
    words: str
    details: list[str]
    yields: list[float]
    done_at: datetime | None

    @classmethod
    def of(cls, found: View) -> AlwaysView:
        switch = found.switch
        return cls(
            available=found.available,
            enabled=switch.enabled,
            set_at=switch.set_at,
            set_by=switch.set_by,
            threshold_pct=switch.threshold_pct,
            recrawl_weak=switch.recrawl_weak,
            waiting_for=found.waiting_for,
            counts=AlwaysCounts(**found.counts),
            pages=found.pages,
            part_pages=found.part_pages,
            words=found.words,
            details=found.details,
            yields=found.yields,
            done_at=found.done_at,
        )


class AlwaysBody(BaseModel):
    """Пустить или остановить, порог «мало рекламодателей» (0–100 %, шаг 0,01) и переобход слабых.
    Не названное остаётся как стоит. Лишнее поле — отказ."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    threshold_pct: float | None = None
    recrawl_weak: bool | None = None

    @field_validator("threshold_pct")
    @classmethod
    def _threshold_in_range(cls, value: float | None) -> float | None:
        problem = None if value is None else threshold_problem(value)
        if problem is not None:
            raise PydanticCustomError("threshold_out_of_range", problem)
        return value


class WeakBody(BaseModel):
    """Пометка донора руками: `true` — пометить, `false` — снять (идёт при любом пороге), `null` —
    вернуть оценку порогу."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    weak: bool | None


async def _view(session: AsyncSession) -> AlwaysView:
    return AlwaysView.of(await always_view.view(session, now=datetime.now(UTC)))


def _changes(was: Switch, now: Switch) -> dict[str, Any]:
    """Что сменилось — «было → стало» словами журнала; несменившееся в журнал не идёт."""
    pairs = (
        ("обход 24/7", STATE[was.enabled], STATE[now.enabled]),
        ("порог «мало рекламодателей», %", was.threshold_pct, now.threshold_pct),
        ("переобход слабых", RECRAWL[was.recrawl_weak], RECRAWL[now.recrawl_weak]),
    )
    return {name: {"было": old, "стало": new} for name, old, new in pairs if old != new}


@router.get("", response_model=AlwaysView, summary="Обход 24/7: состояние и очереди")
async def read_always(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AlwaysView:
    """Идёт ли обход 24/7, почему стоит, сколько сайтов в каждой очереди и что пройдено."""
    return await _view(session)


@router.post("", response_model=AlwaysView, summary="Пустить или остановить обход 24/7")
async def set_always(
    body: AlwaysBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> AlwaysView:
    """Выключатель, порог и переобход — новой строкой истории и записью журнала; то же, что стоит,
    — без записи. Рубильник сервиса выключен — отказ словами."""
    if not cfg.ENABLED:
        raise HTTPException(status.HTTP_409_CONFLICT, SWITCHED_OFF)
    was, now = await always_switch.set_switch(
        session,
        by=author.id,
        enabled=body.enabled,
        threshold_pct=body.threshold_pct,
        recrawl_weak=body.recrawl_weak,
    )
    changed = _changes(was, now)
    if changed:
        await AccessRepository(session).record(
            AuditAction.CRAWL_ALWAYS_CHANGED, author_id=author.id, target=TARGET, details=changed
        )
    await session.commit()
    return await _view(session)


@router.post("/weak", response_model=DonorDepthView, summary="Пометить донора руками")
async def set_weak(
    body: WeakBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> DonorDepthView:
    """Пометка «мало рекламодателей» руками — сильнее порога; `null` возвращает оценку порогу.
    Смена — в журнал. Донора нет — отказ словами."""
    host = crawl_host(body.host)
    if not await always_view.known_donor(session, host):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DONOR)
    was, _ = await always_depth.set_weak(
        session, host, body.weak, by=author.id, now=datetime.now(UTC)
    )
    if was != body.weak:
        await AccessRepository(session).record(
            AuditAction.CRAWL_ALWAYS_CHANGED,
            author_id=author.id,
            target=f"domain:{host}",
            details={"мало рекламодателей": {"было": MARK[was], "стало": MARK[body.weak]}},
        )
    threshold = (await always_switch.state(session)).threshold_pct
    found = await always_view.depths(session, [host], threshold=threshold)
    await session.commit()
    return DonorDepthView.of(found[host])
