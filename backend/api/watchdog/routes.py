"""Сторож тишины на экране.

Смотрят все, у кого есть доступ к базе: тревога «ответов нет ни одного»
касается не админа, а того, кто каждый день ждёт этих ответов. Тревоги о почте
продаж — только с правом «Продажи» (решение Anthony 10.10.2026, П2б): сторож
считает этапы, которые видит учётка, и каждая тревога несёт свой этап.

**Провайдеров — после транзакции запроса и только «Обзору»** (ревью стыков R7, 7.4). С #355
маршрут спрашивает и полоса тревог над вкладками «Продаж» — при открытии раздела и каждые
5 минут на каждой вкладке, — а опрос провайдеров идёт десятки секунд (таймауты Ahrefs 30 с,
выдачи 60 с). Тревоги базы считаются и фиксируются до опроса: соединение не висит «idle in
transaction» на время чужих ответов, как и у фонового сторожа (`silence.with_providers`).
Полоса этапа (`?stage=`) — тревоги своего этапа, без опроса: «провайдер не отвечает» — тревога
общая, её место на «Обзоре». Ответ провайдеров экрану годен минуту (`PROBES`). Тревога — по
правилу `silence.ProviderWatch` процесса сервера, как у фонового сторожа: неответ, прошедший
сам, «Обзор» не красит.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.features.access.permissions import visible_stages
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import alarms, probe_providers

router = APIRouter(prefix="/watchdog", tags=["сторож тишины"])

_viewer = Depends(needs(Permission.VIEW))

#: Сколько ответ провайдеров годен экрану: «Обзор» открыт бывает на нескольких вкладках, и каждая
#: спрашивает раз в 5 минут, а провайдер один и отвечает до полутора минут.
PROBE_TTL_SEC = 60.0


@dataclass
class Probes:
    """Ответ провайдеров экрану — свежий или прежний, не старше `PROBE_TTL_SEC`. В памяти
    процесса: у каждого процесса сервера — свой. Часы монотонные; срок — от ответа, а не от
    вопроса: ответ медленного опроса не устаревает, едва придя. Фоновый сторож спрашивает своим
    порядком (`workers/reaper.Providers`)."""

    clock: Callable[[], float] = time.monotonic
    answered_at: float | None = None
    answer: Alarm | None = None

    async def probe(self) -> Alarm | None:
        """Тревога «провайдер не отвечает» — или `None`: все ответили."""
        if self.answered_at is None or self.clock() - self.answered_at >= PROBE_TTL_SEC:
            self.answer = await probe_providers()
            self.answered_at = self.clock()
        return self.answer


#: Ответ провайдеров экрану сторожа — один на процесс; тесты начинают с пустого (`conftest.py`).
PROBES = Probes()


class AlarmCard(BaseModel):
    """Одна тревога: что молчит и что это значит."""

    code: str
    title: str
    detail: str
    #: Этап, о почте которого тревога: экран не показывает тревогу продаж без права
    #: «Продажи», даже пришедшую раньше, чем право сняли. Общая тревога — без этапа.
    stage: Stage | None = None

    @classmethod
    def of(cls, alarm: Alarm) -> AlarmCard:
        return cls(code=alarm.code, title=alarm.title, detail=alarm.detail, stage=alarm.stage)


class WatchdogView(BaseModel):
    """Что сейчас молчит не по делу. Пусто — тихо и правильно."""

    alarms: list[AlarmCard]


@router.get("", response_model=WatchdogView, summary="Тишина, которая означает поломку")
async def silence(
    stage: Stage | None = None,
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> WatchdogView:
    """Тревоги «Обзора» — или полосы этапа (`stage`)."""
    seen = visible_stages(actor(user))
    if stage is not None:
        return await _strip(session, seen & {stage}, stage)
    found = list(await alarms(session, stages=seen))
    # Тревоги базы посчитаны: транзакция закрывается до опроса — соединение не ждёт провайдеров.
    await session.commit()
    # Два бесплатных запроса к провайдерам: «API недоступен» — то, ради чего «Обзор» и открывают.
    unreachable = await PROBES.probe()
    if unreachable is not None:
        found.insert(0, unreachable)
    return WatchdogView(alarms=[AlarmCard.of(alarm) for alarm in found])


async def _strip(session: AsyncSession, seen: frozenset[Stage], stage: Stage) -> WatchdogView:
    """Полоса над вкладками раздела этапа: тревоги этого этапа, без опроса провайдеров. Этап,
    которого учётка не видит (`seen` без него), — пусто."""
    found = await alarms(session, stages=seen)
    return WatchdogView(alarms=[AlarmCard.of(one) for one in found if one.stage is stage])
