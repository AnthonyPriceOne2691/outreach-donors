"""Сторож нагрузки обходчика: новый обход — только под свободные ядра машины.

Обходчиков 16 (решение Anthony 29.10.2026, было 8), а машина общая: на ней база, API,
воркеры очередей и соседний сервис. Правило Anthony: «соседний сервис работает и ядра
заняты — обходчики берут, сколько свободно; свободны все — берут все 16». Поэтому сколько
обходов идёт разом — не только числом копий (`CRAWL_WORKERS`), но и свободными ядрами:
прежде чем взять следующую задачу, копия смотрит, есть ли на машине место ещё под один
обход, и если нет — ждёт. **Идущий обход сторож не трогает**: прерванный обход дороже
дождавшегося, а новая работа и так подождёт в очереди.

**Мера — свободные ядра сейчас, а не «ядро на обход».** Свободно — простой процессора по
`/proc/stat` за последний отрезок (простой и ожидание диска), умноженный на число ядер: это
то, что не заняли ни сосед, ни база, ни уже идущие обходы. Бронировать каждому обходу целое
ядро было бы слишком строго: обход почти всё время ждёт — паузу сайта (не меньше секунды) и
его ответ, — а разбор страницы занимает доли секунды; бронь держала бы обходчики при
простаивающей машине. Мера по факту сама даёт правило Anthony: машина свободна — стартуют
все 16; сосед занял половину — стартует столько, сколько влезает в оставшееся; обходы
потяжелели (включили браузер, страницы большие) — влезает меньше, при обходе в целое ядро —
ровно половина. Новому обходу нужно `CRAWL_GATE_CORES_PER_CRAWL` (0,75 — потолок копии в
`docker-compose.prod.yml`: больше обход взять не может) плюс запас `CRAWL_GATE_RESERVE_CORES`
(1 ядро — базе, API и соседу на всплеск). Память — порогом: свободной (`MemAvailable`)
меньше `CRAWL_GATE_MIN_FREE_GB` — ждём. В контейнере без lxcfs `/proc/stat` и `/proc/meminfo`
— числа хоста, а `os.cpu_count()` — ядра хоста: квота `cpus` контейнера их не меняет. Ноль
в настройке — проверка выключена.

**Старты разнесены.** Ждущие копии смотрят на одну и ту же машину: по одному замеру с
пятью свободными ядрами стартовали бы все разом, и нагрузка их обходов появилась бы в
замере уже после. Поэтому взявшая задачу копия ставит в Redis отметку «только что начала»
на `CRAWL_GATE_STAGGER_SEC` (`shared/queue.mark_started`), и остальные до её конца не
берут — следующий замер уже видит новый обход.

**Чего не прочитать — то не держит.** Нет `/proc` (машина разработчика) или файл не тот —
сторож пропускает и говорит об этом в журнале один раз: обходчик без сторожа хуже обходчика
со сторожем, но лучше обходчика, который стоит, потому что не понял машину.

**Ждущая копия жива и видна.** Опрос раз в `CRAWL_GATE_POLL_SEC` — с ударом о жизни
воркера: проверка здоровья (`workers/health.py`) через восемь минут молчания сочла бы её
зависшей. Отметка «ждёт» с причиной — в Redis (`shared/queue.mark_held`), её читает экран
«Обход доноров». Пустая очередь копию не держит и не отмечает: ждать нечего. Выкатка во
время ожидания: просьба остановиться у rq поднимает исключение прямо из сна, и копия без
задачи выходит сразу, сняв отметку.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from redis.exceptions import RedisError

from backend.config import crawl as cfg
from backend.shared.queue import clear_held, mark_held, mark_started, started_recently

logger = logging.getLogger(__name__)

STAT = Path("/proc/stat")
MEMINFO = Path("/proc/meminfo")
#: Пустая очередь: через сколько секунд копия смотрит в неё снова.
QUEUE_POLL_SEC = 5.0
#: Простой за отрезок короче этого — не мера: первый замер после паузы берётся заново.
SAMPLE_SEC = 1.0
#: Прошлый замер старше этого — меряем заново, а не за полчаса простоя очереди.
SAMPLE_MAX_AGE_SEC = 60.0
_KIB_IN_GIB = 1024 * 1024

#: Что сторож уже сказал о непрочитанном `/proc`: говорит один раз за жизнь процесса.
_TOLD: set[str] = set()


@dataclass(frozen=True, slots=True)
class Room:
    """Можно ли брать работу и почему — словами для журнала и экрана."""

    free: bool
    words: str


@dataclass(frozen=True, slots=True)
class Machine:
    """Машина сейчас: свободных ядер и памяти; `None` — не прочитали."""

    free_cores: float | None
    free_gib: float | None


class _Waiter(Protocol):
    """Чего сторож ждёт от воркера: имя для отметки и удар о жизни."""

    name: str

    def heartbeat(self) -> Any: ...


def _decimal(value: float, digits: int = 2) -> str:
    """«0,93» — запятой, как на экране; целое — без дробной части."""
    if digits and float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}".replace(".", ",")


def _cores(value: float) -> str:
    """«1,2 ядра», «1 ядро», «2 ядра», «5 ядер» — число и слово при нём."""
    if not float(value).is_integer():
        return f"{_decimal(value, 1)} ядра"
    whole = int(value)
    tens, ones = whole % 100, whole % 10
    word = (
        "ядер"
        if 11 <= tens <= 14
        else {1: "ядро", 2: "ядра", 3: "ядра", 4: "ядра"}.get(ones, "ядер")
    )
    return f"{whole} {word}"


def judge(machine: Machine, *, per_crawl: float, reserve: float, min_free_gb: float) -> Room:
    """Есть ли место ещё под один обход. Не прочитанное и выключенное (0) не держит."""
    busy = []
    need = per_crawl + reserve
    cores = machine.free_cores
    if per_crawl > 0 and cores is not None and cores < need:
        busy.append(
            f"свободно {_cores(cores)}, а новому обходу нужно {_decimal(need)} "
            f"({_decimal(per_crawl)} на обход и {_decimal(reserve)} в запас)"
        )
    free = machine.free_gib
    if min_free_gb > 0 and free is not None and free < min_free_gb:
        busy.append(f"свободно {_decimal(free, 1)} ГБ памяти при пороге {_decimal(min_free_gb, 1)}")
    if busy:
        return Room(False, "; ".join(busy))
    return Room(True, "машина свободна")


def cpu_times(text: str | None) -> tuple[float, float] | None:
    """(простой с ожиданием диска, всего) из строки `cpu` `/proc/stat`; `None` — не разобрали.
    Всего — user…steal: guest уже внутри user, второй раз его не считаем."""
    if not text or not text.startswith("cpu "):
        return None
    try:
        fields = [float(value) for value in text.split("\n", 1)[0].split()[1:9]]
    except ValueError:
        logger.debug("сторож нагрузки: строка cpu /proc/stat не разобрана — %r", text[:80])
        return None
    if len(fields) < 5:
        return None
    return fields[3] + fields[4], sum(fields)


def free_gib(text: str | None) -> float | None:
    """`MemAvailable` из `/proc/meminfo`, ГБ; `None` — строки нет или она не разобрана."""
    for line in (text or "").splitlines():
        if not line.startswith("MemAvailable:"):
            continue
        try:
            return int(line.split()[1]) / _KIB_IN_GIB
        except (IndexError, ValueError):
            logger.debug("сторож нагрузки: строка MemAvailable не разобрана — %r", line[:80])
            return None
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        logger.debug("сторож нагрузки: %s не прочитан — %s", path, exc)
        return None


class CpuMeter:
    """Свободные ядра между двумя замерами `/proc/stat`. Замера нет или он старый — меряет
    заново за `SAMPLE_SEC`; иначе — за отрезок с прошлого (обычно опрос сторожа)."""

    def __init__(
        self,
        read: Callable[[], str | None],
        *,
        cpus: int,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read, self._cpus, self._sleep, self._clock = read, cpus, sleep, clock
        self._last: tuple[float, float, float] | None = None  # простой, всего, когда

    def _take(self) -> tuple[float, float, float] | None:
        times = cpu_times(self._read())
        return None if times is None else (*times, self._clock())

    def free_cores(self) -> float | None:
        now = self._take()
        if now is None:
            return None
        last = self._last
        if last is None or not SAMPLE_SEC <= now[2] - last[2] <= SAMPLE_MAX_AGE_SEC:
            self._sleep(SAMPLE_SEC)
            last, now = now, self._take()
            if now is None:
                return None
        self._last = now
        total = now[1] - last[1]
        if total <= 0:
            return None
        return self._cpus * max(0.0, min(1.0, (now[0] - last[0]) / total))


_meter = CpuMeter(lambda: _read(STAT), cpus=os.cpu_count() or 1)


def read_room(
    *,
    meter: CpuMeter | None = None,
    meminfo: Callable[[], str | None] = lambda: _read(MEMINFO),
) -> Room:
    """Машина сейчас — по порогам настроек (читаются при каждом вызове)."""
    cores = (meter or _meter).free_cores()
    memory = free_gib(meminfo())
    if (cores is None or memory is None) and "unreadable" not in _TOLD:
        _TOLD.add("unreadable")
        logger.info("сторож нагрузки: %s или %s не прочитать — копии не держу", STAT, MEMINFO)
    return judge(
        Machine(cores, memory),
        per_crawl=cfg.GATE_CORES_PER_CRAWL,
        reserve=cfg.GATE_RESERVE_CORES,
        min_free_gb=cfg.GATE_MIN_FREE_GB,
    )


def _span(seconds: float) -> str:
    if seconds < 60:
        return f"{round(seconds)} с"
    return f"{round(seconds / 60)} мин"


def wait_for_room(
    worker: _Waiter,
    *,
    read: Callable[[], Room] = read_room,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Ждать, пока на машине не будет места ещё под один обход; вернуть, сколько ждали, с.
    Место есть сразу — ноль, и ни одного обращения к Redis."""
    room = read()
    if room.free:
        return 0.0
    started = told = clock()
    redis = getattr(worker, "connection", None)
    logger.info(
        "обходчик %s: машина занята — %s; новый обход не беру, жду", worker.name, room.words
    )
    try:
        while True:
            mark_held(worker.name, room.words, redis=redis)
            worker.heartbeat()
            sleep(cfg.GATE_POLL_SEC)
            room = read()
            if room.free:
                break
            if clock() - told >= cfg.GATE_LOG_EVERY_SEC:
                told = clock()
                logger.info(
                    "обходчик %s: жду уже %s — %s", worker.name, _span(told - started), room.words
                )
    finally:
        clear_held(worker.name, redis=redis)
    waited = clock() - started
    logger.info("обходчик %s: место нашлось через %s — беру работу", worker.name, _span(waited))
    return waited


class _Listener(_Waiter, Protocol):
    """Воркер rq глазами сторожа: его очереди."""

    queues: list[Any]


def work_queued(worker: _Listener) -> bool:
    """Ждёт ли в очередях воркера хоть одна задача. Redis не ответил — «да»: дальше
    rq сам повторит связь, а сторож тишину Redis за пустую очередь не примет."""
    try:
        return any(queue.count for queue in worker.queues)
    except RedisError as exc:
        logger.warning("сторож нагрузки: длину очереди не узнать — %s", exc)
        return True


def pause(worker: _Waiter, seconds: float, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Подождать, оставаясь живой для проверки здоровья."""
    worker.heartbeat()
    sleep(seconds)


def just_started(worker: _Waiter) -> bool:
    """Соседняя копия только что взяла обход — ждём, пока его нагрузка попадёт в замер."""
    if cfg.GATE_STAGGER_SEC <= 0:
        return False
    return started_recently(redis=getattr(worker, "connection", None))


def took_work(worker: _Waiter) -> None:
    """Копия взяла обход: отметка «только что начала» на `CRAWL_GATE_STAGGER_SEC`."""
    if cfg.GATE_STAGGER_SEC > 0:
        mark_started(cfg.GATE_STAGGER_SEC, redis=getattr(worker, "connection", None))
