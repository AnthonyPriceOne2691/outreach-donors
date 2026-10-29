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
**Память — процесса:** перезапуск разбора начинает счёт заново. У автоотправки при письмах в
окне — и своя тревога о простое (`autosend_jobs.Watch`: «не шлёт дольше 15 минут… проход падает
— причина»). Дубль оставлен намеренно (ревью 13.10): своя молчит, пока писем в окне нет, сразу
после выкатки и при сбое вне её прохода — тишина хуже дубля.

**Негодная настройка продаж — тоже тревогой, одной на процесс разбора** (`SETTINGS`): с ней модуль
продаж выключен целиком (`config/sales.BROKEN`) — тумблеры ложны, здоровье процессов зелёное,
сторожа почты и тишины продажи пропускают, и автоотправка, агент и передача лида стояли бы
молча, со строкой в stderr. Говорит первый проход любого цикла продаж — один: три цикла
стартуют разом, и пока тревога уходит, остальные её не дублируют (`Settings.sending`); Telegram не
принял — следующий проход.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.config import alerts as alerts_cfg
from backend.config import sales as sales_cfg
from backend.features.ops.alarms import Concern, cause, marked
from backend.features.runs.reasons import explained
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Столько проходов подряд с ошибкой — тревога. Столько же, сколько у здоровья процесса
#: (`workers/health.FAILURES_UNHEALTHY`): тревога приходит тогда, когда раньше краснел процесс.
FAILURES_ALARM = 3

#: Проход цикла — как у `workers/ticker.every`.
Pass = Callable[[], Awaitable[None]]

#: Начало тревоги о негодной настройке продаж.
SETTINGS_SAID = "ПРОДАЖИ ВЫКЛЮЧЕНЫ — настройка с ошибкой"


def alarm(name: str, failures: int, exc: BaseException) -> str:
    """Тревога о цикле продаж словами: какой, сколько подряд и почему."""
    return (
        f"тревога: цикл продаж «{name}» падает {failures}-й проход подряд — {explained(exc)}; "
        "трассировка — в журнале процесса разбора, разбор прогонов и сторож тишины идут"
    )


def resumed(name: str) -> str:
    return f"прошло: цикл продаж «{name}» снова идёт"


#: Пометки тревог последней строкой (`ops/alarms.py`).
CRASHED = cause("работа этого цикла продаж стоит", "найти трассу в журнале процесса разбора")
MISSET = cause(
    "автоотправка, агент продаж и передача лида стоят",
    "поправить настройку и пересоздать контейнеры",
)


def settings_alarm() -> str:
    """Тревога о негодной настройке продаж: имена, значения и что годится (`config/sales.BROKEN`)."""
    return (
        f"{SETTINGS_SAID}: {'; '.join(sales_cfg.BROKEN.values())}. Автоотправка, агент продаж и "
        "передача лида стоят, пока администратор её не поправит"
    )


@dataclass
class Settings:
    """Тревога о негодной настройке продаж — одна на процесс разбора: настройки читаются при его
    старте и до перезапуска те же. Telegram не принял — скажет следующий проход."""

    told: bool = False
    #: Тревога уходит сейчас: три цикла стартуют разом, и проверка «сказано ли» отделена от
    #: итога ожиданием Telegram — без флага ушли бы три, а чужой отказ откатил бы «сказано».
    sending: bool = False

    async def tell(self) -> None:
        if self.told or self.sending or not sales_cfg.BROKEN:
            return
        self.sending = True
        try:
            self.told = await _said(settings_alarm(), MISSET)
        finally:
            self.sending = False


#: Одна на процесс: её спрашивают все циклы продаж — скажет первый проход первого.
SETTINGS = Settings()


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
        await SETTINGS.tell()
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
                self.told = await _said(alarm(self.name, self.failures, exc), CRASHED)
            return
        self.failures = 0
        if self.told and await _said(resumed(self.name), None):
            self.told = False


def own(work: Pass, name: str) -> Own:
    """Проход `work` цикла продаж `name` — под своей памятью (шапка модуля)."""
    return Own(work, name)


async def _said(text: str, concern: Concern | None) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот тревог не задан
    (`send_alert` пишет её сам), как у тревоги автоотправки; пометка — последней строкой, у
    «прошло» её нет."""
    sent = await send_alert(marked(text, concern))
    return sent or not (alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
