"""Проход автоотправки по расписанию (`tick`): тумблеры, сессия и почта на проход, тревога и журнал.

Живёт циклом процесса разбора (`workers/reaper.py`), рядом с повтором передачи лидов: проходу
нужна сессия раз в минуту, а не свой контейнер. Модуль продаж процесс разбора грузит первым
проходом цикла и только при включённых тумблерах — их цикл смотрит до импорта: сбой импорта —
сбой одного цикла, а выключенная автоотправка не грузит в разбор ни отправку, ни почту.

**Работает только при включённых продажах и своём тумблере** (`SALES_ENABLED`,
`SALES_AUTOSEND`): иначе проход не открывает даже сессии. Предохранитель почты продаж не снят —
не включается и при тумблере (`autosend.guard`): сессии не открывает, журнал говорит почему.
Продажи не подключены (нет учётки, отписки, «Отправителя») — проход стоит словами
(`autosend.Facts.stop`).

**Тревога — ТЗ «через 15 минут».** Есть письма в окне получателя и свободный кап, а проход стоит
не по делу дольше `ALARM_AFTER`: исход письма неизвестен, платформа отказывает любому письму или
проход падает. Тогда — одна тревога в общий канал (`shared/alerts.send_alert`), «снова идёт» —
второй, и только по смене состояния: проход раз в минуту не повторяет сказанного. Стоит по делу
(кап набран, вне окна у всех, ящики на паузе) — не тревога: так проход и должен ждать. **Счёт 15
минут — от первого прохода, который стоит не по делу,** а не от последнего ушедшего письма: любой
другой проход счёт обнуляет. Иначе пауза ящика дольше 15 минут копила бы минуты, и первая же
чужая отправка, застигнутая проходом, или один упавший проход давали бы тревогу, а минутой
позже — «прошло». Упавший проход не знает, есть ли письма в окне, — судит по последнему удачному.

**Память — процесса**, как у ленты сторожа до её зеркала в Redis: перезапуск разбора (выкатка)
начинает счёт 15 минут заново, тревога о том, что ещё стоит, приходит после него ещё раз, а
«снова идёт», случившееся между процессами, не приходит.

**Журнал.** Строка на каждое письмо (`autosend.run`) и на остановку прохода — при смене её
вида, а не на каждом проходе: ночью проход стоит «вне окна» часами. Пауза ящиков — обычный
ход, а не остановка: строки о ней нет. Отказ по письму — строкой при смене причины у письма.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import alerts as alerts_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.runs.reasons import explained
from backend.features.sales import autosend
from backend.features.sales.autosend import STALLS, Pass, Stop
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Сколько проход может стоять не по делу при письмах в окне и свободном капе до тревоги.
ALARM_AFTER = timedelta(minutes=15)
#: Чем начинается тревога и её отбой: о чём речь — без открытия журнала.
ALARM = (
    "тревога: автоотправка писем продаж не шлёт дольше 15 минут при письмах в окне "
    "получателя и свободном капе"
)
RESUMED = "прошло: автоотправка писем продаж снова идёт"
#: Громкой строкой журнала: стоит не по делу — или включена, а не идёт (`autosend.guard`).
LOUD = STALLS | {Stop.GUARDED}


@dataclass
class Watch:
    """Память прохода в процессе разбора: с какой минуты он стоит не по делу, что сказано
    тревогой и журналом."""

    #: С какой минуты проход стоит не по делу при письмах в окне и свободном капе — подряд.
    stalled_since: datetime | None = None
    #: Последнее известное «обязан слать» — для прохода, упавшего раньше, чем это выяснил.
    due: bool = False
    #: Почему стоял, когда сказана тревога; `None` — тревоги нет.
    told: str | None = None
    #: Вид последней остановки, сказанной журналом.
    said: Stop | None = None
    #: Письмо → причина отказа, сказанная журналом.
    refused: dict[int, str] = field(default_factory=dict)

    async def see(self, found: Pass, now: datetime) -> None:
        """Удачный проход: журнал по смене, счёт остановки, тревога или отбой."""
        self._journal(found)
        self.due = found.due
        stall = found.stopped if found.stop in STALLS else None
        self._count(stalled=stall is not None, now=now)
        await self._alarm(stall, now)

    async def failed(self, exc: BaseException, now: datetime) -> None:
        """Проход упал — стоит не по делу при последнем известном «обязан слать». Трассировку
        пишет цикл прохода (`workers/ticker.every`)."""
        self._count(stalled=True, now=now)
        await self._alarm(f"проход падает — {explained(exc)}; трассировка в журнале разбора", now)

    def _count(self, *, stalled: bool, now: datetime) -> None:
        """Счёт настоящей остановки: идёт, пока проходы подряд стоят не по делу при письмах в
        окне; ушло письмо, ящики на паузе, кап, отказы по письмам, писем в окне нет — обнуляет."""
        if not (stalled and self.due):
            self.stalled_since = None
        elif self.stalled_since is None:
            self.stalled_since = now

    async def _alarm(self, stall: str | None, now: datetime) -> None:
        """Тревога — один раз, когда проход стоит не по делу дольше `ALARM_AFTER` при письмах
        в окне; отбой — один раз, когда перестал. Telegram не принял — скажет следующий проход."""
        if stall is None:
            if self.told is not None and await _said(f"{RESUMED} (стояла: {self.told})"):
                self.told = None
            return
        late = self.stalled_since is not None and now - self.stalled_since >= ALARM_AFTER
        if late and self.told is None and await _said(f"{ALARM}. {stall}"):
            self.told = stall

    def _journal(self, found: Pass) -> None:
        """Остановка прохода — строкой при смене вида; отказ по письму — при смене причины."""
        if found.stop is not self.said:
            if found.stop is not None and found.stop is not Stop.RESTING:
                level = logging.WARNING if found.stop in LOUD else logging.INFO
                logger.log(level, "продажи: автоотправка стоит — %s", found.stopped)
            self.said = found.stop
        for letter_id in found.sent:
            self.refused.pop(letter_id, None)
        for letter_id, why in found.refused.items():
            if self.refused.get(letter_id) != why:
                logger.info("продажи: автоотправка — письмо №%s не ушло: %s", letter_id, why)
                self.refused[letter_id] = why


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот тревог не задан
    (`send_alert` пишет её сам), как у ленты сторожа (`ops/alarm_feed.py`)."""
    sent = await send_alert(text)
    return sent or not (alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)


#: Память прохода — одна на процесс разбора.
WATCH = Watch()


async def tick(now: datetime | None = None, watch: Watch | None = None) -> None:
    """Проход по расписанию: тумблеры, своя сессия и почта, затем память — журнал и тревога.
    Предохранитель почты не снят — прохода нет, журнал по смене говорит почему. Упал — тревога
    по памяти и исключение дальше: цикл прохода считает неудачи подряд."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOSEND):
        return
    moment, memory = now or datetime.now(UTC), watch or WATCH
    guarded = autosend.guard()
    if guarded is not None:
        await memory.see(Pass(stop=Stop.GUARDED, stopped=guarded), moment)
        return
    try:
        found = await _pass(moment)
    except Exception as exc:
        await memory.failed(exc, moment)
        raise
    await memory.see(found, moment)


async def _pass(now: datetime) -> Pass:
    """Своя сессия и свои транспорты на проход — закрытые после (`transport_factory.in_use`)."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, in_use(Transports()) as transports:
            return await autosend.run(session, transports, now=now)
    finally:
        await engine.dispose()
