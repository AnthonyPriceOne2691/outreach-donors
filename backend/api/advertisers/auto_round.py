"""Автокруг рекламодателей на экране: выключатель, суточный потолок писем и итог последнего круга.

Круг (`crawl/auto_round.py`) сам делает то, что человек делал тремя кнопками после ночи, — только
когда выключатель включён. Здесь — то, что видит и чем управляет человек.

**Права.** Смотреть — под `view`: итог круга — числа и слова. Включать, выключать и менять потолок —
под правами всех трёх кнопок разом: `run` (перевод и поиск адресов тратят деньги) и `send` (сборка и
отправка — письма с наших доменов). Включённый выключатель — те же кнопки, нажатые заранее на каждый
день.

**Смена — в журнал действий** («было → стало», кто): с этой минуты сервис отправляет сам, и по
журналу видно, кто это разрешил и кто остановил. Та же настройка второй раз записи не заводит.

**Рубильник сервиса выключен** (`config/auto_round`) — выключателя нет: экран говорит об этом
словами, смена — отказ словами.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.config import auto_round as cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import auto_round_switch, auto_round_view
from backend.features.crawl.auto_round_switch import FEWEST, MOST, Switch
from backend.features.crawl.auto_round_view import RoundSummary, RoundView

router = APIRouter(prefix="/auto-round")

_viewer = Depends(needs(Permission.VIEW))

#: Выключатель в журнале действий — словами.
SWITCH = {True: "включён", False: "выключен"}
#: Рубильник сервиса выключен — смена выключателя отказом.
SWITCHED_OFF = (
    "Автокруг выключен настройкой сервиса: выключателя на экране нет, пока его не включат на "
    "сервере. Перевод, сборка и отправка — кнопками"
)


async def switcher(
    user: UserModel = Depends(needs(Permission.RUN)),
    _sends: UserModel = Depends(needs(Permission.SEND)),
) -> UserModel:
    """Кто включает и выключает автокруг: право запуска и право отправки разом (шапка модуля)."""
    return user


class SwitchView(BaseModel):
    """Выключатель как он стоит. Не ставили ни разу — выключен, потолок по умолчанию, `set_by` и
    `set_at` пусты."""

    enabled: bool
    letters: int
    set_by: str | None
    set_at: datetime | None

    @classmethod
    def of(cls, found: Switch) -> SwitchView:
        return cls(
            enabled=found.enabled, letters=found.letters, set_by=found.set_by, set_at=found.set_at
        )


class RoundSummaryView(BaseModel):
    """Последний круг. `words` — строка после «Автокруг 17.11, 08:12:»: итог, что делается или
    почему встал; время `at` экран пишет по часам смотрящего. `going` — круг ещё идёт. `lines` —
    строки для «i»."""

    day: date
    status: str
    at: datetime | None
    words: str
    going: bool
    lines: list[str]

    @classmethod
    def of(cls, summary: RoundSummary) -> RoundSummaryView:
        return cls(**asdict(summary))


class AutoRoundView(BaseModel):
    """Автокруг на экране. `available` — рубильник сервиса включён: без него выключателя нет.
    `ran_today` — круг этой даты уже был; `first` — когда пойдёт первый круг, если включить сейчас.
    `letters_min` и `letters_max` — границы суточного потолка: экран проверяет поле ими, а не своей
    копией."""

    available: bool
    switch: SwitchView
    last: RoundSummaryView | None
    ran_today: bool
    first: str
    letters_min: int
    letters_max: int

    @classmethod
    def of(cls, found: RoundView) -> AutoRoundView:
        return cls(
            available=found.available,
            switch=SwitchView.of(found.switch),
            last=None if found.last is None else RoundSummaryView.of(found.last),
            ran_today=found.ran_today,
            first=found.first,
            letters_min=FEWEST,
            letters_max=MOST,
        )


class SwitchBody(BaseModel):
    """Включить или выключить автокруг и его суточный потолок писем. Лишнее поле — отказ."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    letters: int

    @field_validator("letters")
    @classmethod
    def _letters_in_range(cls, letters: int) -> int:
        if not FEWEST <= letters <= MOST:
            raise PydanticCustomError(
                "letters_out_of_range",
                f"Писем в сутки — от {FEWEST} до {MOST}, пришло {letters}: больше сборка "
                "за раз не берёт",
            )
        return letters


async def _view(session: AsyncSession) -> AutoRoundView:
    return AutoRoundView.of(await auto_round_view.view(session, now=datetime.now(UTC)))


def _changes(was: Switch, now: Switch) -> dict[str, Any]:
    """Что сменилось — «было → стало»; несменившееся в журнал не идёт."""
    details: dict[str, Any] = {}
    if was.enabled != now.enabled:
        details["автокруг"] = {"было": SWITCH[was.enabled], "стало": SWITCH[now.enabled]}
    if was.letters != now.letters:
        details["писем в сутки"] = {"было": was.letters, "стало": now.letters}
    return details


@router.get("", response_model=AutoRoundView, summary="Автокруг: выключатель и последний круг")
async def read_round(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AutoRoundView:
    """Как стоит выключатель, что автокруг сделал последним кругом и когда пойдёт первый."""
    return await _view(session)


@router.post("", response_model=AutoRoundView, summary="Включить или выключить автокруг")
async def set_round(
    body: SwitchBody,
    author: UserModel = Depends(switcher),
    session: AsyncSession = Depends(db_session),
) -> AutoRoundView:
    """Выключатель и потолок — новой строкой истории и записью журнала; то же, что стоит, — без
    записи. Рубильник сервиса выключен — отказ словами."""
    if not cfg.ENABLED:
        raise HTTPException(status.HTTP_409_CONFLICT, SWITCHED_OFF)
    was, now = await auto_round_switch.set_switch(
        session, on=body.enabled, letters=body.letters, by=author.id
    )
    changed = _changes(was, now)
    if changed:
        await AccessRepository(session).record(
            AuditAction.ADVERTISER_AUTO_ROUND_CHANGED,
            author_id=author.id,
            target="advertisers:auto-round",
            details=changed,
        )
    await session.commit()
    return await _view(session)
