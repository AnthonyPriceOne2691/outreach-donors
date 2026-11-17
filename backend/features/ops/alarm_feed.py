"""Тревоги сторожа — человеку в Telegram, по смене состояния (Ф4, срез 4.5b).

Тревоги сторожа тишины шли в журнал и на экран (`GET /api/watchdog`), но не в Telegram.
Теперь каждая новая и каждая прошедшая уходит общим каналом (`shared/alerts.py`,
`ALERT_TELEGRAM_*`), как у `scripts/healthwatch.sh`: о разнице с прошлым проходом, а не на
каждом — иначе не читают ни одного. **Бот не настроен** — громкая строка «ТРЕВОГА НЕ
ОТПРАВЛЕНА» в журнале, тоже по смене состояния. **Telegram не принял** — следующий проход
скажет ещё раз.

**Род — одним сообщением** (слово владельца 17.11.2026: «нужно, чтобы алерты стали адекватнее и
информативнее»): пятнадцать ящиков продаж молчали по одной причине, и пришло тридцать сообщений —
пятнадцать «тревога» и пятнадцать «прошло». Тревоги одного рода (`Alarm.kind`, его задаёт
правило), сменившие состояние в одном проходе, — одно сообщение со списком; так же «прошло».
Род «тревоги» — вместе с пометкой: она одна строка, последняя, и тревоги рода с разной пометкой
идут разными сообщениями. Без рода — сообщение на тревогу. Слова — `alarm_words.py`.

**С какого времени и сколько.** «Тревога» называет минуту первого прохода, который её увидел,
даже если Telegram принял её позже; «прошло» — когда она шла и сколько. Конец — первый проход
без неё, а не минута «прошло»: «прошло» ждёт срок тишины (у сторожа — 20 минут), и длительность
врала бы на этот срок. Часы — системные (`wall_clock`), а не монотонные срока тишины: начало
переживает перезапуск.

**Где смотреть** — экран тревоги (`Alarm.screen`), а у тревоги без своего — экран ленты
(`Feed.screen`): у сторожа «Обзор», где видны все его тревоги.

**Дребезг.** «Тревога» — сразу, «прошло» — когда тревоги нет `QUIET_PASSES` проходов подряд, а
у ленты со сроком тишины (`Feed.quiet_sec`) — ещё и столько секунд с прохода, где её видели:
тревога, которая мигает (ящик на часовом потолке добивок), — одно «тревога», а не сообщение на
каждом проходе. Вернулась раньше — счёт сначала, и ни одного сообщения. Срок — временем, а не
числом проходов: у сторожа проход стал чаще (`workers/reaper.QUIET_SEC`), и счёт проходов
ослабил бы защиту от мигания во столько же раз (ревью #337). Код, о котором проход не спрашивал
(`tell(…, unseen=…)`: прежний ответ провайдеров между опросами), — не наблюдение: ни счёт, ни
срок по нему не идут, и «прошло» о нём скажет только свежий ответ.

**Сказанное переживает перезапуск.** Код тревоги и её заголовок лежат хэшем в Redis
(`TOLD_KEY`; reaper и так ходит в Redis за живостью задач): тревога, кончившаяся за выкаткой
процесса сторожа, получает своё «прошло», а действующая не приходит второй раз. Redis не
ответил — строка журнала и память процесса, как до хранилища: прочитать сказанное прежним
процессом лента попробует следующим проходом, записать своё — тоже.

**Заметки — рядом, своим хэшем** (`notes_key`): с какого времени тревога идёт, её род и имя в
списке рода — JSON на код, той же транзакцией, что сказанное. Хэш сказанного остался «код →
заголовок»: запись прежней версии читается как была — «прошло» о ней без времени, как раньше, —
а прежняя версия после отката читает своё и о заметки не спотыкается. Род тревоги, сказанной
без заметки, лента берёт с первого прохода, который её видит, и её «прошло» уходит со своим родом.

**Сказанное — по ленте и по каналу.** Строка журнала — не сообщение в Telegram: у журнала и у
каждого чата свой хэш (`told_key`). Владелец задал бота или сменил чат и перезапустил процесс —
новый канал пуст, и действующие тревоги приходят туда один раз: включение бота — сводка «что
сломано сейчас». И у каждой ленты свой хэш, по её имени (`Feed.name`): в процессе разбора говорят
две — сторож (`workers/reaper.FEED`) и бот продаж (`sales/agent/notify.BOT_FEED`). С общим хэшем
одна брала тревоги другой за свои и через `QUIET_PASSES` проходов говорила «прошло» о тревоге,
которая идёт, а запись каждой стирала сказанное другой. Лента без имени (`UNNAMED`) в сервисе
одна, имена не повторяются — держит тест (`tests/test_alarm_feed.py`). Прежний общий хэш
(`outreach:watch:told:<канал>`) не читает никто (решение владельца 12.10.2026).

**Пометка — последней строкой, «прошло» — без неё** (слово владельца 29.10.2026). «Тревога»
кончается строкой «есть ли повод для беспокойства» (`alarms.marked`); тревога без пометки идёт,
как шла. «Прошло» пометки не несёт: оно само говорит, что причины больше нет, а «повода больше
нет» соврало бы о тревоге, у которой повода не было, и о прежнем уровне, пока действует
следующий («прошло» о 80 % капа приходит, когда идёт 95 %).

**Повод появился — тревога ещё раз.** Где повод зависит от числа, правило решает его каждым
проходом под тем же кодом. Сказанная «повода нет» тревога, у которой повод появился, приходит
снова — с ним и с прежним началом; обратно, когда повод ушёл, лента молчит до «прошло»: хорошая
новость подождёт, а мигание не станет потоком сообщений. Сказанную прежним процессом тревогу
первый проход только запоминает: каким был её повод, он не знает, а повтор после каждой выкатки
приучил бы не читать.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from redis import Redis
from redis.exceptions import RedisError

from backend.config import alerts as alerts_cfg
from backend.config import storage
from backend.features.ops import alarm_words
from backend.features.ops.alarm_words import Over, Raised
from backend.features.ops.alarms import Alarm
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Сколько проходов подряд тревоги нет, прежде чем сказать «прошло», — у каждой ленты: одно
#: наблюдение без тревоги — ещё не конец. Лента бота продаж (проход раз в 5 минут) этим и
#: ограничивается; у сторожа (проход раз в 2 минуты) решает срок тишины `Feed.quiet_sec`.
QUIET_PASSES = 2

#: Где сказанное ждёт следующий процесс: хэш «код тревоги → заголовок» на ленту и канал —
#: `outreach:watch:told:<лента>:<чат Telegram>`, пока бот не задан — `…:<лента>:journal`
#: (`told_key`). Ключ без имени ленты — прежний общий хэш: его не читает никто.
TOLD_KEY = "outreach:watch:told"

#: Хвост ключа заметок к сказанному (`notes_key`): хэш «код тревоги → заметка JSON» рядом с
#: хэшем сказанного.
NOTES = "notes"

#: Канал, пока бот не задан: слово ленты — громкая строка журнала (`shared/alerts.py`).
JOURNAL = "journal"

#: Имя ленты, которой его не дали (`Feed()`). Такая лента в сервисе одна: вторая без имени
#: делила бы с ней сказанное.
UNNAMED = "unnamed"

#: Сколько ждать Redis. Сторож живёт в одном процессе с разбором прогонов, клиент Redis
#: синхронный: зависший Redis не должен держать и их.
REDIS_TIMEOUT_SEC = 5.0


def connection() -> Redis:
    """Клиент Redis на одно обращение, с таймаутами. Отдельной функцией — её подменяют
    тесты (`tests/conftest.py`): сказанное из теста в общий Redis не уходит."""
    return Redis.from_url(
        storage.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SEC,
        socket_timeout=REDIS_TIMEOUT_SEC,
    )


def wall_clock() -> datetime:
    """Нынешнее время по системным часам, UTC: с какого времени тревога и сколько она шла.
    Отдельной функцией — её закрепляет щит тестов (`tests/conftest.py`): слова ленты называют
    время, и текст не должен зависеть от минуты прогона."""
    return datetime.now(UTC)


def told_key(name: str = UNNAMED) -> str:
    """Хэш сказанного ленты `name` для канала этого процесса: чат Telegram — или журнал, пока
    бот не задан. Канал — из настроек процесса: смена бота или чата идёт перезапуском, и новый
    канал начинает с пустого хэша."""
    return f"{TOLD_KEY}:{name}:{alerts_cfg.TELEGRAM_CHAT_ID if _bot_set() else JOURNAL}"


def notes_key(name: str = UNNAMED) -> str:
    """Хэш заметок к сказанному ленты `name` — того же канала, рядом с хэшем сказанного."""
    return f"{told_key(name)}:{NOTES}"


@dataclass(frozen=True, slots=True)
class Note:
    """Что лента помнит о сказанной тревоге сверх заголовка — для «прошло»: с какого времени она
    идёт (`None` — не знает: сказана до заметок), её род и имя в списке рода."""

    since: datetime | None = None
    kind: str = ""
    item: str = ""


@dataclass
class Feed:
    """Тревоги, о которых уже сказано: код → заголовок и заметка; в Redis — зеркало для
    перезапуска.

    Своё имя — своё сказанное (`told_key`): две ленты с одним именем делили бы хэш, и одна
    говорила бы «прошло» о тревогах другой, которые идут."""

    #: Назначение ленты — часть ключа её хэша. Без имени — `UNNAMED`.
    name: str = UNNAMED
    #: Сколько секунд сказанной тревоги не должно быть с прохода, где её видели, прежде чем
    #: сказать «прошло» (вдобавок к `QUIET_PASSES`). 0 — хватает проходов.
    quiet_sec: float = 0.0
    #: Часы срока тишины — монотонные: перевод системных часов срок не сбивает.
    clock: Callable[[], float] = time.monotonic
    #: Где смотреть тревоги ленты без своего экрана (`Alarm.screen`): у сторожа — «Обзор», где они
    #: все. Пусто — строки «где смотреть» у таких тревог нет.
    screen: str = ""
    told: dict[str, str] = field(default_factory=dict)
    #: С какого времени сказанная тревога идёт, её род и имя — для «прошло»; в Redis — своим хэшем
    #: рядом со сказанным (`notes_key`).
    notes: dict[str, Note] = field(default_factory=dict)
    #: Сколько проходов подряд сказанной тревоги нет (в памяти: перезапуск начинает счёт заново).
    quiet: dict[str, int] = field(default_factory=dict)
    #: Когда проход видел сказанную тревогу в последний раз (в памяти: после перезапуска срок
    #: идёт от первого прохода без неё).
    seen: dict[str, float] = field(default_factory=dict)
    #: Первый проход, который не увидел сказанную тревогу, — её конец в словах «прошло» (в памяти:
    #: после перезапуска — первый проход без неё).
    ended: dict[str, datetime] = field(default_factory=dict)
    #: Первый проход, который увидел ещё не сказанную тревогу: Telegram не принял — её начало не
    #: сдвигается.
    fresh: dict[str, datetime] = field(default_factory=dict)
    #: Был ли повод у сказанной тревоги: был «нет», а стал «есть» — она приходит ещё раз (в
    #: памяти: тревогу, сказанную прежним процессом, судит первый проход, который её застал).
    caused: dict[str, bool] = field(default_factory=dict)
    #: Прочитано ли сказанное прежним процессом. До того зеркало не пишется: затёрло бы его.
    recalled: bool = False
    #: Память разошлась с Redis (запись не удалась или ещё не читали) — дописать.
    unsaved: bool = False
    #: Запись в этом проходе не удалась — до его конца Redis не ждём: дописывает следующий проход.
    stalled: bool = False

    async def tell(self, found: Sequence[Alarm], *, unseen: Collection[str] = ()) -> None:
        """Сказать о новых тревогах сразу и о прошедших — когда их нет `QUIET_PASSES` проходов
        подряд и `quiet_sec` секунд; тревоги одного рода — одним сообщением. `unseen` — коды, о
        которых проход не спрашивал: прежний ответ — не наблюдение, счёт и срок тишины по ним
        стоят."""
        self.stalled = False
        self._recall()
        moment, at = self.clock(), wall_clock()
        now = {alarm.code: alarm for alarm in found}
        await self._new(now, at)
        self._saw([alarm for code, alarm in now.items() if code not in unseen], moment)
        await self._gone({*now, *unseen}, moment, at)

    async def _new(self, now: Mapping[str, Alarm], at: datetime) -> None:
        """Новые тревоги — сразу; сказанные без повода, у которых он появился, — ещё раз. Род —
        одним сообщением (`_kind_of`)."""
        self.fresh = {code: self.fresh.get(code, at) for code in now if code not in self.told}
        due = [alarm for code, alarm in now.items() if self._due(code, alarm)]
        for batch in _batches(due, _kind_of):
            raised = [Raised(self._screened(alarm), self._since(alarm.code)) for alarm in batch]
            if await _said(alarm_words.alarmed(raised, at)):
                self._remember(raised)

    def _screened(self, alarm: Alarm) -> Alarm:
        """Тревога с экраном ленты, если своего у неё нет."""
        return alarm if alarm.screen or not self.screen else replace(alarm, screen=self.screen)

    def _due(self, code: str, alarm: Alarm) -> bool:
        """Сказать ли о тревоге: новая — да; сказанная — если у неё появился повод. Сказанную
        прежним процессом первый проход только запоминает (`caused`)."""
        if code not in self.told:
            return True
        was = self.caused.setdefault(code, _caused(alarm))
        return not was and _caused(alarm)

    def _since(self, code: str) -> datetime | None:
        """С какого времени тревога идёт: сказанная — по заметке, новая — с прохода, который
        увидел её первым."""
        if code in self.told:
            return self.notes.get(code, Note()).since
        return self.fresh.get(code)

    def _remember(self, raised: Sequence[Raised]) -> None:
        """Тревоги сообщения сказаны: заголовок, повод и заметка — в память и в зеркало."""
        for one in raised:
            code = one.alarm.code
            self.told[code], self.caused[code] = one.alarm.title, _caused(one.alarm)
            self.notes[code] = Note(one.since, *_named(one.alarm))
            self.fresh.pop(code, None)
        self._save()

    def _saw(self, alarms: Iterable[Alarm], moment: float) -> None:
        """Сказанные тревоги, которые проход видел: счёт и срок тишины — заново; род и имя в
        заметке — нынешние (тревога, сказанная до заметок, получает их здесь)."""
        renamed = False
        for alarm in alarms:
            if alarm.code in self.told:
                self.quiet.pop(alarm.code, None)
                self.ended.pop(alarm.code, None)
                self.seen[alarm.code] = moment
                renamed |= self._renamed(alarm)
        if renamed:
            self._save()

    def _renamed(self, alarm: Alarm) -> bool:
        """Род и имя тревоги в заметке — нынешние; `True` — сменились."""
        was = self.notes.get(alarm.code, Note())
        kind, item = _named(alarm)
        self.notes[alarm.code] = Note(was.since, kind, item)
        return (kind, item) != (was.kind, was.item)

    async def _gone(self, kept: Collection[str], moment: float, at: datetime) -> None:
        """Прошедшие — когда тишина вышла (`_over`); род — одним сообщением. `kept` — коды,
        которые проход видел или о которых не спрашивал: о них «прошло» не говорится."""
        over = [code for code in self.told if code not in kept and self._over(code, moment, at)]
        for batch in _batches(over, self._kind_told):
            if await _said(alarm_words.passed([self._ended_as(code, at) for code in batch], at)):
                for code in batch:
                    self._forget(code)
                self._save()

    def _over(self, code: str, moment: float, at: datetime) -> bool:
        """Ещё проход без тревоги `code`; вышла ли тишина — `QUIET_PASSES` проходов подряд и
        `quiet_sec` секунд с прохода, где её видели. Первый такой проход — её конец (`ended`)."""
        self.quiet[code] = self.quiet.get(code, 0) + 1
        self.ended.setdefault(code, at)
        since = self.seen.setdefault(code, moment)
        return self.quiet[code] >= QUIET_PASSES and moment - since >= self.quiet_sec

    def _kind_told(self, code: str) -> str | None:
        """Ключ сообщения «прошло» — род сказанной тревоги; без рода — сообщение на тревогу."""
        return self.notes.get(code, Note()).kind or None

    def _ended_as(self, code: str, at: datetime) -> Over:
        """Прошедшая тревога для слов «прошло»: заголовок, заметка и конец."""
        note = self.notes.get(code, Note())
        return Over(self.told[code], note.since, self.ended.get(code, at), note.kind, note.item)

    def _forget(self, code: str) -> None:
        """О прошедшей тревоге лента не помнит ничего."""
        del self.told[code], self.quiet[code], self.seen[code]
        self.caused.pop(code, None)
        self.notes.pop(code, None)
        self.ended.pop(code, None)

    def _recall(self) -> None:
        """Сказанное прежним процессом — один раз, когда Redis ответит; сказанное этим
        процессом до того — поверх (оно новее). Разошлось — дописать зеркало."""
        if not self.recalled:
            stored = _stored(self.name)
            if stored is None:
                return
            told, notes = stored
            self.told, self.notes = {**told, **self.told}, {**notes, **self.notes}
            self.recalled = True
        if self.unsaved:
            self._save()

    def _save(self) -> None:
        """Зеркало памяти — в Redis. Не записалось — до конца прохода больше не пробовать
        (зависший Redis держал бы по таймауту каждое слово), дописать в начале следующего."""
        self.unsaved = True
        if self.recalled and not self.stalled:
            self.unsaved = self.stalled = not _stored_as(self.name, self.told, self.notes)


def _batches[T](items: Iterable[T], key: Callable[[T], Hashable | None]) -> list[list[T]]:
    """Сообщения прохода: у кого ключ рода один — одно сообщение, без ключа — по одному; порядок —
    по первой тревоге сообщения."""
    found: list[list[T]] = []
    kinds: dict[Hashable, list[T]] = {}
    for item in items:
        mark = key(item)
        if mark is None:
            found.append([item])
        elif mark in kinds:
            kinds[mark].append(item)
        else:
            kinds[mark] = [item]
            found.append(kinds[mark])
    return found


def _kind_of(alarm: Alarm) -> tuple[str, str] | None:
    """Ключ сообщения «тревоги» — род и пометка: пометка одна строка, последняя, и тревоги рода с
    разной пометкой идут разными сообщениями. Без рода — сообщение на тревогу."""
    if alarm.kind is None:
        return None
    return alarm.kind.title, "" if alarm.concern is None else alarm.concern.words


def _named(alarm: Alarm) -> tuple[str, str]:
    """Род тревоги и её имя в списке рода — для заметки."""
    return ("" if alarm.kind is None else alarm.kind.title), alarm.item


def _stored(name: str) -> tuple[dict[str, str], dict[str, Note]] | None:
    """Сказанное ленты `name` и заметки к нему из Redis. `None` — Redis не ответил (строка
    журнала)."""
    key = told_key(name)
    try:
        with connection() as redis:
            raw, noted = redis.hgetall(key), redis.hgetall(notes_key(name))
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное из Redis не прочитано (%s) — помню только этот процесс, "
            "повтор следующим проходом",
            exc,
            extra={"key": key},
        )
        return None
    told = {_text(code): _text(title) for code, title in _pairs(raw)}
    notes = {_text(code): _note(value) for code, value in _pairs(noted)}
    return told, {code: note for code, note in notes.items() if code in told}


def _stored_as(name: str, told: Mapping[str, str], notes: Mapping[str, Note]) -> bool:
    """Записать сказанное и заметки к нему целиком (одной транзакцией Redis). `False` — не
    записалось."""
    key, noted = told_key(name), notes_key(name)
    kept = {code: _dumped(note) for code, note in notes.items() if code in told and note != Note()}
    try:
        with connection() as redis, redis.pipeline() as pipe:
            pipe.delete(key)
            pipe.delete(noted)
            if told:
                pipe.hset(key, mapping=dict(told))
            if kept:
                pipe.hset(noted, mapping=kept)
            pipe.execute()
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное в Redis не записано (%s) — повтор следующим проходом",
            exc,
            extra={"key": key},
        )
        return False
    return True


def _pairs(raw: object) -> Iterable[tuple[object, object]]:
    """Пары ответа `hgetall`; не словарь — ни одной."""
    return raw.items() if isinstance(raw, dict) else ()


def _dumped(note: Note) -> str:
    since = None if note.since is None else note.since.isoformat()
    return json.dumps({"since": since, "kind": note.kind, "item": note.item}, ensure_ascii=False)


def _note(raw: object) -> Note:
    """Заметка из хэша. Чужая или битая запись — пустая заметка и строка журнала: «прошло» уйдёт
    без времени, а проход сторожа не упадёт."""
    try:
        found = json.loads(_text(raw))
        return Note(
            _moment(found.get("since")), str(found.get("kind") or ""), str(found.get("item") or "")
        )
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning(
            "лента тревог: заметка о сказанной тревоге не разобрана (%s) — «прошло» без времени",
            exc,
        )
        return Note()


def _moment(raw: object) -> datetime | None:
    """Время заметки — ISO-строкой; без пояса — UTC. Пусто — лента не знает."""
    if not raw:
        return None
    moment = datetime.fromisoformat(str(raw))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _text(raw: object) -> str:
    """Строка из ответа Redis. Чужое значение не UTF-8 — со знаками замены: `UnicodeDecodeError`
    не `RedisError` и ронял бы каждый проход сторожа."""
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _caused(alarm: Alarm) -> bool:
    """Есть ли у тревоги повод для беспокойства. Без пометки (тревога продаж) — есть: такой
    тревоге лента не говорит ни «повода нет», ни «повод появился»."""
    return alarm.concern is None or alarm.concern.cause


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот не настроен."""
    sent = await send_alert(text)
    return sent or not _bot_set()


def _bot_set() -> bool:
    return bool(alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
