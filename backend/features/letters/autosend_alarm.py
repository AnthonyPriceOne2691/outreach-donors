"""Тревога, когда очередь, которая уходит сама (`autosend.py`), стоит не по делу дольше 15 минут.

Как у автоотправки продаж (ТЗ «через 15 минут», `sales/autosend_jobs.py`): проход стоит не по
делу — почта отказала любому письму, исход письма неизвестен, каждое письмо окна получило отказ или
проход падает (`autosend.Passed.stall`). Стоит по делу — кап набран, ящики на паузе, вне окна у
всех получателей, очередь пуста — не тревога: так проход и должен ждать.

**Канал — общий** (`shared/alerts.send_alert`: бот тревог `ALERT_TELEGRAM_*`, тот же, что у
автоотправки продаж и сторожа тишины). Одна тревога на случай и одна строка «прошло», когда
проход снова идёт, — по смене состояния: проход раз в минуту сказанного не повторяет. Telegram не
принял — скажет следующий проход.

**Повод для беспокойства есть всегда** (пометка последней строкой, слово Anthony 29.10.2026,
`ops/alarms.marked`): стоит не по делу — значит, сама очередь не пойдёт, пока причину не уберут.
«Прошло» пометки не несёт — оно само говорит, что причины больше нет.

**Счёт 15 минут — от первого прохода, который стоит не по делу,** подряд: любой другой проход счёт
обнуляет. Иначе пауза ящика копила бы минуты, и одна чужая отправка, застигнутая проходом, давала
бы тревогу, а минутой позже — «прошло».

**Память — процесса разбора**: перезапуск (выкатка) начинает счёт заново, и тревога о том, что ещё
стоит, приходит после него ещё раз. Остановленный запуск забывается без «прошло»: его остановил
человек или кончилась очередь — строка под кнопкой пачки это и говорит.

Своя память, а не продаж: почта модуль продаж не импортирует (контракт
`mail-does-not-know-sales`), а их память ещё и знает их виды остановки.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.features.core.domain import Stage
from backend.features.crawl.niche import WHOM
from backend.features.ops.alarms import cause, marked
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Сколько проход может стоять не по делу, прежде чем тревога.
ALARM_AFTER = timedelta(minutes=15)

#: Пометка тревоги: очередь стоит не по делу — причина под кнопкой пачки.
STALLED = cause(
    "очередь стоит не по делу", "разобрать причину под кнопкой «Отправить очередь» на «Письмах»"
)


def title_of(stage: Stage, audience: str) -> str:
    """Чья очередь — словами тревоги: «очередь писем донорам», «… рекламодателям по найденной
    ссылке», «… бизнесам ниши»."""
    whom = "донорам" if stage is Stage.DONORS else WHOM.get(audience, audience)
    return f"очередь писем {whom}"


@dataclass(slots=True)
class _Case:
    """Один запуск: с какой минуты стоит не по делу подряд и что сказано тревогой."""

    since: datetime | None = None
    told: str | None = None


@dataclass(slots=True)
class Watch:
    """Память прохода в процессе разбора: запуск → его случай."""

    cases: dict[int, _Case] = field(default_factory=dict)

    async def see(self, mode_id: int, title: str, stall: str | None, now: datetime) -> None:
        """Проход запуска кончился: стоит не по делу (`stall`) — счёт и, через `ALARM_AFTER`,
        тревога; идёт — счёт обнулён и, если тревога была, «прошло»."""
        case = self.cases.setdefault(mode_id, _Case())
        if stall is None:
            case.since = None
            if case.told is not None and await send_alert(
                f"прошло: {title} снова уходит сама (стояла: {case.told})"
            ):
                case.told = None
            return
        case.since = case.since or now
        if case.told is not None or now - case.since < ALARM_AFTER:
            return
        if await send_alert(
            marked(f"тревога: {title} не уходит сама дольше 15 минут — {stall}", STALLED)
        ):
            case.told = stall
            logger.warning("письма: тревога по запуску №%s — %s", mode_id, stall)

    def keep(self, running: Collection[int]) -> None:
        """Забыть остановленные запуски: их остановил человек или кончилась очередь."""
        for mode_id in set(self.cases) - set(running):
            del self.cases[mode_id]


#: Память проходов этого процесса разбора.
WATCH = Watch()
