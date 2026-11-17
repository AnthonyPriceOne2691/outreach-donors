"""Тревога сторожа об автокруге рекламодателей (`auto_round.py`) — общим механизмом `ops/alarms.py`
и лентой.

**Когда.** Рубильник и выключатель включены, а последний круг упал, не начался за день или стоит на
шаге дольше `STUCK_AFTER`. Круга нет к `CATCH_UP_UNTIL`, хотя выключатель стоял включённым с утра, —
тоже: даже если проход, который ведёт круг, не работает вовсе и строки «пропущен» записать некому.
Повод для беспокойства есть всегда: письма рекламодателям сами не собрались и не ушли. Начался
следующий круг или выключатель выключили — тревога проходит сама.

Своим модулем, без самого круга: сторож живёт в процессе разбора с тесным пределом памяти, и правило
не должно тянуть за собой перевод, почту и очередь задач — только модели и расписание
(`auto_round_report.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import auto_round as cfg
from backend.features.core.domain import Stage
from backend.features.core.llm_caps import MOSCOW
from backend.features.core.models.advertiser import AutoRoundModel
from backend.features.crawl import auto_round_switch
from backend.features.crawl.auto_round_report import CATCH_UP_UNTIL, GOING, OVER, START, Phase
from backend.features.ops.alarms import Alarm, cause

#: Круг стоит на шаге дольше этого — проход, который его ведёт, не работает: поиск ждут не дольше
#: трёх часов, сборка с повторами укладывается в столько же.
STUCK_AFTER = timedelta(hours=8)
#: Код тревоги сторожа.
ALARM = "advertiser-auto-round"
#: Пометка: работа стоит — письма рекламодателям сами не собрались и не ушли.
NOT_DONE = cause(
    "письма рекламодателям сами не собрались и не ушли",
    "посмотреть причину в «К письму» на «Рекламодателях»: кнопки там же, следующий круг начнётся сам",
)
NOT_STARTED = "Автокруг рекламодателей не начался"


def _alarm(title: str, detail: str) -> Alarm:
    return Alarm(code=ALARM, title=title, detail=detail, stage=Stage.ADVERTISERS, concern=NOT_DONE)


def _of_row(row: AutoRoundModel, moment: datetime) -> Alarm | None:
    """Тревога по строке круга: упал, не начался или стоит на шаге дольше `STUCK_AFTER`."""
    if row.status == Phase.FAILED.value:
        return _alarm(
            "Автокруг рекламодателей встал", f"Круг {row.day:%d.%m} не дошёл до конца: {row.note}"
        )
    if row.status == Phase.MISSED.value:
        return _alarm(NOT_STARTED, f"Круг {row.day:%d.%m}: {row.note}")
    began = row.started_at or row.created_at
    if row.status not in OVER and moment - began > STUCK_AFTER:
        hours = int(STUCK_AFTER.total_seconds() // 3600)
        return _alarm(
            "Автокруг рекламодателей стоит",
            f"Круг {row.day:%d.%m} {GOING.get(row.status, 'идёт')} дольше {hours} ч: проход, "
            "который его ведёт, похоже, не работает",
        )
    return None


async def alarm(session: AsyncSession, now: datetime | None = None) -> Alarm | None:
    """Правило сторожа (шапка модуля): только при включённых рубильнике и выключателе."""
    if not cfg.ENABLED:
        return None
    switch = await auto_round_switch.state(session)
    if not switch.enabled:
        return None
    moment = now or datetime.now(UTC)
    local = moment.astimezone(MOSCOW)
    last: AutoRoundModel | None = await session.scalar(
        select(AutoRoundModel).order_by(AutoRoundModel.day.desc()).limit(1)
    )
    if last is not None and last.day >= local.date() - timedelta(days=1):
        found = _of_row(last, moment)
        if found is not None or last.day == local.date():
            return found
    return _not_started(switch.on_since, local)


def _not_started(since: datetime | None, local: datetime) -> Alarm | None:
    """Круга даты нет к `CATCH_UP_UNTIL`, а выключатель стоял включённым с её утра."""
    owed = since is not None and since < datetime.combine(local.date(), START, tzinfo=MOSCOW)
    if local.time() < CATCH_UP_UNTIL or not owed:
        return None
    return _alarm(
        NOT_STARTED,
        f"Круга {local:%d.%m} не было до {CATCH_UP_UNTIL:%H:%M} по Москве: процесс разбора "
        "не работал или обходчики стояли",
    )
