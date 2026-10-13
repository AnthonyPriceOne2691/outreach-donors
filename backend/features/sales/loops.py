"""Циклы продаж в процессе разбора: их неудачи — тревога продаж, а не здоровье процесса.

Процесс разбора (`workers/reaper.py`) крутит рядом с разбором мёртвых прогонов и сторожем тишины
три прохода продаж: повтор передачи лидов, автоотправку и автосборку. Здоровье процесса
(`workers/health.beat_problems`) краснеет, когда любой его цикл падает три прохода подряд, — и
сломанная автосборка делала «нездоровым» весь процесс, где живёт сторож тишины: красный процесс
прятал бы за собой его настоящую остановку, а тревога хоста о процессе говорила бы не о продажах.

**Проход продаж — под своей памятью (`own`).** Исключение прохода — и ленивого импорта модуля
продаж в нём — в цикл не выходит: строка журнала с трассировкой, счёт неудач подряд, на третьей —
тревога продаж словами в общий канал тревог (`shared/alerts.send_alert`), первый удачный проход
после неё — «прошло». Отметка цикла — без неудач: здоровье процесса судит разбор и сторожа.
**Зависание** проход продаж по-прежнему показывает отметкой цикла: проход, который не кончился,
не прервать без риска — письмо, отправленное посреди прохода, на повторе ушло бы второй раз.

**Тревога — по смене состояния**, как у тревоги автоотправки (`autosend_jobs.Watch`): одна на
серию неудач и одно «прошло»; Telegram не принял — скажет следующий проход. Бот тревог не задан —
`send_alert` пишет тревогу громкой строкой журнала, и повторять её каждым проходом незачем.
**Память — процесса:** перезапуск разбора начинает счёт заново.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.config import alerts as alerts_cfg
from backend.features.runs.reasons import explained
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Столько проходов подряд с ошибкой — тревога. Столько же, сколько у здоровья процесса
#: (`workers/health.FAILURES_UNHEALTHY`): тревога приходит тогда, когда раньше краснел процесс.
FAILURES_ALARM = 3

#: Проход цикла — как у `workers/ticker.every`.
Pass = Callable[[], Awaitable[None]]


def alarm(name: str, failures: int, exc: BaseException) -> str:
    """Тревога о цикле продаж словами: какой, сколько подряд и почему."""
    return (
        f"тревога: цикл продаж «{name}» падает {failures}-й проход подряд — {explained(exc)}; "
        "трассировка — в журнале процесса разбора, разбор прогонов и сторож тишины идут"
    )


def resumed(name: str) -> str:
    return f"прошло: цикл продаж «{name}» снова идёт"


@dataclass
class Own:
    """Проход цикла продаж под своей памятью: неудачи подряд и сказана ли тревога."""

    work: Pass
    name: str
    #: Проходов подряд с ошибкой.
    failures: int = 0
    #: Тревога о текущей серии неудач сказана — «прошло» скажет удачный проход.
    told: bool = False

    async def __call__(self) -> None:
        try:
            await self.work()
        except Exception as exc:
            self.failures += 1
            logger.exception(
                "продажи: цикл «%s» — проход не удался (%s-й подряд), повтор следующим проходом",
                self.name,
                self.failures,
                extra={"loop": self.name, "failures": self.failures},
            )
            if self.failures >= FAILURES_ALARM and not self.told:
                self.told = await _said(alarm(self.name, self.failures, exc))
            return
        self.failures = 0
        if self.told and await _said(resumed(self.name)):
            self.told = False


def own(work: Pass, name: str) -> Own:
    """Проход `work` цикла продаж `name` — под своей памятью (шапка модуля)."""
    return Own(work, name)


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот тревог не задан
    (`send_alert` пишет её сам), как у тревоги автоотправки."""
    sent = await send_alert(text)
    return sent or not (alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
