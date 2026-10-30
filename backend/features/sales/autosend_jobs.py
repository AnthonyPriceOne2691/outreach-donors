"""Проход автоотправки по расписанию (`tick`): тумблеры, сессия и почта на проход, тревога и журнал.

Живёт циклом процесса разбора (`workers/reaper.py`), рядом с повтором передачи лидов: проходу
нужна сессия раз в минуту, а не свой контейнер. Модуль продаж процесс разбора грузит первым
проходом цикла и только при включённых тумблерах — их цикл смотрит до импорта: сбой импорта —
сбой одного цикла, а выключенная автоотправка не грузит в разбор ни отправку, ни почту.

**Работает только при включённых продажах и своём тумблере** (`SALES_ENABLED`,
`SALES_AUTOSEND`): иначе проход не открывает даже сессии. Предохранитель почты продаж не снят —
не включается и при тумблере (`autosend.guard`): сессии не открывает, журнал говорит почему.
Продажи не подключены (нет учётки, отписки, «Отправителя») — проход стоит словами
(`autosend.Facts.stop`), и стоит не по делу: владелец включил продажи и автоотправку, а строку
предохранителя удалили или поле «Отправителя» стёрли. Сторож почты продаж в это время молчит (он
только у подключённых продаж), письма не уходят и не собираются — поэтому тревога через 15 минут
(`OFFLINE`) и без писем в окне: молчать о таком было бы некому. Так же — лимит первых писем продаж
не прочитан из настроек (`Stop.UNREAD`, опечатка в `.env`): ноль первых писем в сутки сам не
пройдёт, а выбор ящика назвал бы это «писать некому», как набранный кап.

**Тревога — ТЗ «через 15 минут».** Есть письма в окне получателя и свободный кап, а проход стоит
не по делу дольше `ALARM_AFTER`: исход письма неизвестен, платформа отказывает любому письму,
каждое письмо окна получило отказ или проход падает. Тогда — одна тревога в общий канал
(`shared/alerts.send_alert`), «снова идёт» — второй, и только по смене состояния: проход раз в
минуту не повторяет сказанного. Стоит по делу (кап набран, вне окна у всех, ящики на паузе,
письма очереди ждут проверки адреса) — не тревога: так проход и должен ждать. **Счёт 15 минут —
от первого прохода, который стоит не по делу,** а не от последнего ушедшего письма: любой другой
проход счёт обнуляет. Иначе пауза ящика дольше 15 минут копила бы минуты, и первая же чужая
отправка, застигнутая проходом, или один упавший проход давали бы тревогу, а минутой позже —
«прошло». Упавший проход не знает, есть ли письма в окне, — судит по последнему удачному.

**Память — процесса**, как у ленты сторожа до её зеркала в Redis: перезапуск разбора (выкатка)
начинает счёт 15 минут заново, тревога о том, что ещё стоит, приходит после него ещё раз, а
«снова идёт», случившееся между процессами, не приходит. Свои неизвестные исходы (почта не
ответила на письмо прохода) — тоже в памяти, до `STUCK_AFTER` (`autosend.unsure`): после
перезапуска их держит блок «Исход неизвестен», с `STUCK_AFTER`.

**Журнал.** Строка на каждое письмо (`autosend.run`) и на остановку прохода — при смене её
вида, а не на каждом проходе: ночью проход стоит «вне окна» часами. Пауза ящиков — обычный
ход, а не остановка: строки о ней нет. Отказ по письму — строкой при смене причины у письма,
с отказом отправки целиком: «письмо не готово к отправке» без него не сказало бы, что чинить.
Письмо, снятое с очереди после отказов платформы (`autosend_refusals.py`), — громкой строкой
с причиной; счёт этих отказов — тоже память процесса (`Watch.bounced`). Письмо, снятое при
отправке, потому что сайт в работе у другого направления, — одной строкой словами снятия: это не
отказ и не остановка, в очереди его больше нет.
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
from backend.features.letters.unknown_outcome import STUCK_AFTER
from backend.features.ops.alarms import Concern, cause, marked
from backend.features.runs.reasons import explained
from backend.features.sales import autosend, autosend_refusals
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
#: Тревога, когда продажи не подключены: писем в окне при этом может и не быть.
OFFLINE = "тревога: автоотправка писем продаж включена, а не шлёт дольше 15 минут"
#: Пометка тревоги последней строкой (`ops/alarms.py`): причину называет сама тревога, и у всех
#: причин одно — письма не уходят, пока её не устранят.
STALLED = cause("автоотправка писем продаж стоит не по делу", "устранить причину, названную выше")
#: Стоит не по делу и без писем в окне: включённой автоотправке продажи нужны подключёнными, а
#: лимит первых писем — прочитанным (`autosend.Facts.broken`).
ALWAYS = frozenset({Stop.NOT_CONNECTED, Stop.UNREAD})
#: Громкой строкой журнала: стоит не по делу — или включена, а не идёт (`autosend.guard`).
LOUD = STALLS | ALWAYS | {Stop.GUARDED}


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
    #: Письма, исход которых проход сам оставил неизвестным, — номер → минута (`autosend.unsure`).
    unsure: dict[int, datetime] = field(default_factory=dict)
    #: Отказы платформы письму, сосчитанные проходами, — номер → сколько (`autosend_refusals.py`).
    bounced: dict[int, int] = field(default_factory=dict)

    async def see(self, found: Pass, now: datetime) -> None:
        """Удачный проход: журнал по смене, счёт остановки, тревога или отбой."""
        self._journal(found)
        if found.unsure is not None:
            self.unsure[found.unsure] = now
        self._bounced(found)
        self.due = found.due or found.stop in ALWAYS
        stall = found.stopped if found.stop in STALLS | ALWAYS else None
        self._count(stalled=stall is not None, now=now)
        await self._alarm(stall, now, OFFLINE if found.stop in ALWAYS else ALARM)

    def remembered(self, now: datetime) -> frozenset[int]:
        """Свои неизвестные исходы моложе `STUCK_AFTER`; старше — их держит блок «Исход
        неизвестен»."""
        self.unsure = {letter: at for letter, at in self.unsure.items() if now - at < STUCK_AFTER}
        return frozenset(self.unsure)

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

    async def _alarm(self, stall: str | None, now: datetime, head: str = ALARM) -> None:
        """Тревога — один раз, когда проход стоит не по делу дольше `ALARM_AFTER` при письмах
        в окне (или без них — `ALWAYS`); отбой — один раз, когда перестал. Telegram не принял —
        скажет следующий проход."""
        if stall is None:
            if self.told is not None and await say(f"{RESUMED} (стояла: {self.told})", None):
                self.told = None
            return
        late = self.stalled_since is not None and now - self.stalled_since >= ALARM_AFTER
        if late and self.told is None and await say(f"{head}. {stall}", STALLED):
            self.told = stall

    def _journal(self, found: Pass) -> None:
        """Остановка прохода — строкой при смене вида; письмо, снятое при отправке, — одной
        строкой словами снятия; отказ по письму — при смене причины."""
        self._stop_said(found)
        for letter_id in found.sent:
            self.refused.pop(letter_id, None)
        for letter_id, words in found.withdrawn.items():
            logger.info("продажи: автоотправка — %s", words, extra={"letter": letter_id})
        for letter_id, why in found.refused.items():
            if self.refused.get(letter_id) != why:
                said = _detailed(why, found.details.get(letter_id))
                logger.info("продажи: автоотправка — письмо №%s не ушло: %s", letter_id, said)
                self.refused[letter_id] = why

    def _stop_said(self, found: Pass) -> None:
        """Остановка прохода — строкой при смене вида; пауза ящиков — не остановка."""
        if found.stop is self.said:
            return
        if found.stop is not None and found.stop is not Stop.RESTING:
            level = logging.WARNING if found.stop in LOUD else logging.INFO
            logger.log(level, "продажи: автоотправка стоит — %s", found.stopped)
        self.said = found.stop

    def _bounced(self, found: Pass) -> None:
        """Отказы платформы письмам — в счёт (`autosend_refusals.py`); письмо, снятое с очереди
        после них, — громкой строкой; ушедшее или снятое (и при отправке) из памяти выходит."""
        self.bounced.update(found.bounced)
        for letter_id, words in found.dropped.items():
            logger.warning("продажи: автоотправка — письмо №%s: %s", letter_id, words)
        for letter_id in (*found.sent, *found.dropped, *found.withdrawn):
            self.bounced.pop(letter_id, None)
            self.refused.pop(letter_id, None)


def _detailed(why: str, detail: str | None) -> str:
    """Отказ по письму для журнала: слова пачки и отказ отправки целиком, если он есть."""
    return f"{why} — {detail}" if detail else why


async def say(text: str, concern: Concern | None) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот тревог не задан
    (`send_alert` пишет её сам), как у ленты сторожа (`ops/alarm_feed.py`). Тем же путём
    говорит и тревога автосборки (`autobuild_alarm.py`). Пометка — последней строкой
    (`ops/alarms.marked`); у «прошло» её нет: оно само говорит, что причины больше нет."""
    sent = await send_alert(marked(text, concern))
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
        with (
            autosend.unsure(memory.remembered(moment)),
            autosend_refusals.before(memory.bounced),
        ):
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
