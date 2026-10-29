"""Сторож нагрузки обходчика: новый обход — только под свободные ядра машины.

Машина подменена текстами `/proc` и часами теста, Redis — словарём в памяти: живой Redis
и настоящая нагрузка здесь не нужны. Проверяется то, ради чего сторож (правило Anthony
29.10.2026): сосед занял половину машины — обходов берётся, сколько влезает (при обходе
в целое ядро — половина); свободно всё — все 16; лёгкие обходы влезают и рядом с соседом.
Ждущая копия жива (удар о жизни) и видна (отметка с причиной), дождавшаяся снимает
отметку; старты разнесены; пустая очередь копию не держит.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from backend.config import crawl as crawl_cfg
from backend.shared import crawl_marks
from backend.workers import crawler, load_gate
from backend.workers.load_gate import CpuMeter, Machine, Room, judge, read_room, wait_for_room
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Worker

MEM_PLENTY = "MemTotal:       64000000 kB\nMemFree:  1000000 kB\nMemAvailable:   40000000 kB\n"
MEM_TIGHT = "MemTotal:       64000000 kB\nMemAvailable:    5242880 kB\n"
FREE = Room(True, "машина свободна")


class FakeRedis:
    """Хэши и ключи в памяти теста; `down` — Redis не отвечает."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.keys: dict[str, int] = {}
        self.down = False

    def _answer(self) -> None:
        if self.down:
            raise RedisConnectionError("выдуманный Redis не отвечает")

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        self._answer()
        return {k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()}

    def hset(self, key: str, field: str, value: str) -> int:
        self._answer()
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hdel(self, key: str, *fields: str) -> int:
        self._answer()
        bucket = self.hashes.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)

    def set(self, key: str, _value: str, *, px: int) -> bool:
        self._answer()
        self.keys[key] = px
        return True

    def exists(self, key: str) -> int:
        self._answer()
        return int(key in self.keys)


class FakeWorker:
    name = "crawler-7"

    def __init__(self, redis: FakeRedis) -> None:
        self.connection = redis
        self.beats = 0

    def heartbeat(self) -> None:
        self.beats += 1


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture(autouse=True)
def thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Умолчания настроек — явно: тест не зависит от `.env` машины."""
    monkeypatch.setattr(crawl_cfg, "GATE_CORES_PER_CRAWL", 0.75)
    monkeypatch.setattr(crawl_cfg, "GATE_RESERVE_CORES", 1.0)
    monkeypatch.setattr(crawl_cfg, "GATE_MIN_FREE_GB", 8.0)
    monkeypatch.setattr(crawl_cfg, "GATE_POLL_SEC", 15.0)
    monkeypatch.setattr(crawl_cfg, "GATE_LOG_EVERY_SEC", 300.0)
    monkeypatch.setattr(crawl_cfg, "GATE_STAGGER_SEC", 20.0)


def started(
    *, neighbour: float, per_crawl_use: float, copies: int = 16, cores: int = 16, **rule: float
) -> int:
    """Сколько копий начнут обход одна за другой: каждая перед стартом спрашивает сторожа,
    и свободно — ядра машины минус сосед минус уже идущие обходы (каждый ест `per_crawl_use`)."""
    rule = {"per_crawl": 0.75, "reserve": 1.0, "min_free_gb": 8.0} | rule
    running = 0
    while running < copies:
        machine = Machine(cores - neighbour - running * per_crawl_use, free_gib=40.0)
        if not judge(machine, **rule).free:
            break
        running += 1
    return running


class TestRule:
    """Правило Anthony: «сосед занял ядра — обходчики берут, сколько свободно; свободны все —
    берут все 16»."""

    def test_neighbour_on_half_the_machine_with_core_hungry_crawls_gives_half(self) -> None:
        assert started(neighbour=8, per_crawl_use=1.0, per_crawl=1.0, reserve=0.0) == 8

    def test_an_idle_machine_takes_all_sixteen(self) -> None:
        assert started(neighbour=0, per_crawl_use=1.0, per_crawl=1.0, reserve=0.0) == 16

    def test_with_the_defaults_crawls_at_their_cap_still_give_about_half(self) -> None:
        # 0,75 ядра — потолок копии, ядро в запас: перед каждым стартом 8 − 0,75·N ≥ 1,75 —
        # девятый ещё влезает (свободно 2), десятый — нет (1,25).
        assert started(neighbour=8, per_crawl_use=0.75) == 9

    def test_light_crawls_fit_beside_the_neighbour_all_sixteen(self) -> None:
        # Обход ждёт паузу сайта и ответ: доля ядра, а не целое — бронь ядра была бы строже.
        assert started(neighbour=8, per_crawl_use=0.25) == 16

    def test_a_machine_taken_almost_whole_starts_nothing(self) -> None:
        assert started(neighbour=15, per_crawl_use=0.25) == 0


class TestJudge:
    def test_room_for_one_more_crawl_and_memory_is_free(self) -> None:
        room = judge(Machine(6.0, 30.0), per_crawl=0.75, reserve=1.0, min_free_gb=8)

        assert room == FREE

    def test_too_few_free_cores_hold_and_say_how_many_are_needed(self) -> None:
        room = judge(Machine(1.2, 30.0), per_crawl=0.75, reserve=1.0, min_free_gb=8)

        assert not room.free
        assert room.words == (
            "свободно 1,2 ядра, а новому обходу нужно 1,75 (0,75 на обход и 1 в запас)"
        )

    def test_little_free_memory_holds_too_and_both_reasons_are_named(self) -> None:
        room = judge(Machine(1.2, 5.0), per_crawl=0.75, reserve=1.0, min_free_gb=8)

        assert room.words.endswith("; свободно 5 ГБ памяти при пороге 8")

    def test_a_fractional_memory_threshold_keeps_its_tenth(self) -> None:
        room = judge(Machine(6.0, 5.04), per_crawl=0.75, reserve=1.0, min_free_gb=6.5)

        assert room.words == "свободно 5,0 ГБ памяти при пороге 6,5"

    @pytest.mark.parametrize(
        ("cores", "said"),
        [(1.0, "1 ядро"), (2.0, "2 ядра"), (5.0, "5 ядер"), (11.0, "11 ядер"), (0.4, "0,4 ядра")],
    )
    def test_cores_are_said_with_the_right_word(self, cores: float, said: str) -> None:
        room = judge(Machine(cores, 30.0), per_crawl=12.0, reserve=0.0, min_free_gb=8)

        assert room.words.startswith(f"свободно {said},")

    def test_zero_switches_a_check_off(self) -> None:
        room = judge(Machine(0.0, 0.5), per_crawl=0.0, reserve=1.0, min_free_gb=0)

        assert room.free

    def test_what_was_not_read_does_not_hold(self) -> None:
        assert judge(Machine(None, None), per_crawl=0.75, reserve=1.0, min_free_gb=8).free


def stat(idle: float, iowait: float, busy: float) -> str:
    """Строка `cpu` `/proc/stat`: user, nice, system, idle, iowait, irq, softirq, steal."""
    return f"cpu  {busy} 0 0 {idle} {iowait} 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n"


class TestCpuMeter:
    def test_the_first_measure_takes_its_own_second(self) -> None:
        texts = iter([stat(1000, 0, 1000), stat(1012, 0, 1004)])
        slept: list[float] = []
        clock = iter([100.0, 101.0])
        meter = CpuMeter(
            lambda: next(texts), cpus=16, sleep=slept.append, clock=lambda: next(clock)
        )

        free = meter.free_cores()

        assert slept == [load_gate.SAMPLE_SEC]
        assert free == pytest.approx(16 * 12 / 16)  # 12 из 16 тиков — простой

    def test_the_next_measure_is_since_the_last_poll_without_a_new_second(self) -> None:
        texts = iter([stat(0, 0, 0), stat(10, 0, 10), stat(10, 2, 26)])
        slept: list[float] = []
        clock = iter([0.0, 1.0, 16.0])
        meter = CpuMeter(
            lambda: next(texts), cpus=16, sleep=slept.append, clock=lambda: next(clock)
        )

        meter.free_cores()
        later = meter.free_cores()

        assert slept == [load_gate.SAMPLE_SEC]
        assert later == pytest.approx(16 * 2 / 18)  # простой и ожидание диска — свободно

    def test_a_stale_last_measure_is_taken_again(self) -> None:
        texts = iter([stat(0, 0, 0), stat(8, 0, 8), stat(8, 0, 100), stat(9, 0, 107)])
        slept: list[float] = []
        clock = iter([0.0, 1.0, 1000.0, 1001.0])
        meter = CpuMeter(
            lambda: next(texts), cpus=16, sleep=slept.append, clock=lambda: next(clock)
        )

        meter.free_cores()
        again = meter.free_cores()

        assert slept == [load_gate.SAMPLE_SEC, load_gate.SAMPLE_SEC]
        assert again == pytest.approx(16 * 1 / 8)

    @pytest.mark.parametrize("text", [None, "", "intr 1 2 3", "cpu  a b c d e", "cpu  1 2 3"])
    def test_what_is_not_parsed_is_not_a_measure(self, text: str | None) -> None:
        meter = CpuMeter(lambda: text, cpus=16, sleep=lambda _s: None, clock=lambda: 0.0)

        assert meter.free_cores() is None

    def test_no_ticks_between_measures_is_not_a_measure(self) -> None:
        clock = iter([0.0, 1.0])
        meter = CpuMeter(
            lambda: stat(5, 0, 5), cpus=16, sleep=lambda _s: None, clock=lambda: next(clock)
        )

        assert meter.free_cores() is None


class TestReadRoom:
    def test_uses_the_thresholds_of_the_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        texts = iter([stat(0, 0, 0), stat(2, 0, 14), stat(2, 0, 14), stat(4, 0, 28)])
        clock = iter([0.0, 1.0, 0.0, 1.0])
        meter = CpuMeter(
            lambda: next(texts), cpus=16, sleep=lambda _s: None, clock=lambda: next(clock)
        )

        busy = read_room(meter=meter, meminfo=lambda: MEM_PLENTY)  # 16·2/16 = 2 свободных ядра
        monkeypatch.setattr(crawl_cfg, "GATE_RESERVE_CORES", 1.5)
        held = read_room(
            meter=CpuMeter(
                lambda: next(texts), cpus=16, sleep=lambda _s: None, clock=lambda: next(clock)
            ),
            meminfo=lambda: MEM_PLENTY,
        )

        assert busy.free  # 2 свободных ядра ≥ 0,75 + 1
        assert not held.free  # 2 < 0,75 + 1,5
        assert held.words.startswith("свободно 2 ядра")

    def test_memory_comes_from_mem_available(self) -> None:
        meter = CpuMeter(lambda: None, cpus=16, sleep=lambda _s: None, clock=lambda: 0.0)

        room = read_room(meter=meter, meminfo=lambda: MEM_TIGHT)

        assert room.words == "свободно 5 ГБ памяти при пороге 8"

    def test_no_proc_is_said_once_and_does_not_hold(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(load_gate, "_TOLD", set())
        caplog.set_level(logging.INFO, logger="backend.workers.load_gate")
        meter = CpuMeter(lambda: None, cpus=16, sleep=lambda _s: None, clock=lambda: 0.0)

        rooms = [read_room(meter=meter, meminfo=lambda: None) for _ in range(3)]

        assert all(room.free for room in rooms)
        assert sum("не прочитать" in record.getMessage() for record in caplog.records) == 1

    @pytest.mark.parametrize("line", ["MemAvailable: много kB", "MemAvailable:"])
    def test_a_broken_mem_available_line_does_not_hold(self, line: str) -> None:
        assert load_gate.free_gib(f"MemTotal: 1 kB\n{line}\n") is None


class TestWaitForRoom:
    def test_a_free_machine_does_not_wait_and_does_not_touch_redis(self, redis: FakeRedis) -> None:
        redis.down = True  # обращение к Redis уронило бы тест
        worker = FakeWorker(redis)

        waited = wait_for_room(worker, read=lambda: FREE)

        assert (waited, worker.beats) == (0.0, 0)

    def test_waits_alive_and_visible_then_takes_work_and_clears_the_mark(
        self, redis: FakeRedis, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="backend.workers.load_gate")
        busy = Room(
            False, "свободно 1,2 ядра, а новому обходу нужно 1,75 (0,75 на обход и 1 в запас)"
        )
        rooms = iter([busy, busy, busy, FREE])
        now = [1000.0]
        seen: list[dict[str, Any]] = []
        worker = FakeWorker(redis)

        def sleep(seconds: float) -> None:
            seen.append(json.loads(redis.hashes[crawl_marks.CRAWL_HELD_KEY][worker.name]))
            now[0] += seconds

        waited = wait_for_room(worker, read=lambda: next(rooms), sleep=sleep, clock=lambda: now[0])

        assert waited == 45.0
        assert worker.beats == 3  # удар о жизни на каждый опрос: здоровье не сочтёт зависшей
        assert [mark["words"] for mark in seen] == [busy.words] * 3
        assert redis.hashes[crawl_marks.CRAWL_HELD_KEY] == {}  # дождалась — отметки нет
        said = [record.getMessage() for record in caplog.records]
        assert any("машина занята" in line and "не беру" in line for line in said)
        assert any("место нашлось через 45 с" in line for line in said)

    def test_a_long_wait_is_logged_every_few_minutes_not_every_poll(
        self, redis: FakeRedis, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="backend.workers.load_gate")
        busy = Room(False, "свободно 5 ГБ памяти при пороге 8")
        rooms = iter([busy] * 80 + [FREE])  # 80 опросов × 15 с = 20 мин
        now = [0.0]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        wait_for_room(
            FakeWorker(redis), read=lambda: next(rooms), sleep=sleep, clock=lambda: now[0]
        )

        still = [r.getMessage() for r in caplog.records if "жду уже" in r.getMessage()]
        assert still == [
            "обходчик crawler-7: жду уже 5 мин — свободно 5 ГБ памяти при пороге 8",
            "обходчик crawler-7: жду уже 10 мин — свободно 5 ГБ памяти при пороге 8",
            "обходчик crawler-7: жду уже 15 мин — свободно 5 ГБ памяти при пороге 8",
        ]

    def test_a_stop_request_during_the_wait_leaves_no_mark(self, redis: FakeRedis) -> None:
        busy = Room(False, "свободно 5 ГБ памяти при пороге 8")

        class StopRequestedError(Exception):
            """Как `rq.worker.StopRequested`: поднимается обработчиком SIGTERM из сна."""

        def sleep(_seconds: float) -> None:
            raise StopRequestedError

        with pytest.raises(StopRequestedError):
            wait_for_room(FakeWorker(redis), read=lambda: busy, sleep=sleep, clock=lambda: 0.0)

        assert redis.hashes[crawl_marks.CRAWL_HELD_KEY] == {}

    def test_redis_down_does_not_stop_the_wait(self, redis: FakeRedis) -> None:
        redis.down = True
        busy = Room(False, "свободно 5 ГБ памяти при пороге 8")
        rooms = iter([busy, busy, FREE])
        worker = FakeWorker(redis)

        wait_for_room(worker, read=lambda: next(rooms), sleep=lambda _s: None)

        assert worker.beats == 2


class TestMarks:
    def test_fresh_held_marks_are_counted_with_the_newest_reason(self, redis: FakeRedis) -> None:
        crawl_marks.mark_held("crawler-1", "свободно 1 ядра…", redis=redis, now=100)
        crawl_marks.mark_held("crawler-2", "свободно 0,5 ядра…", redis=redis, now=110)

        assert crawl_marks.crawlers_held(redis, now=120) == crawl_marks.Held(
            2, "свободно 0,5 ядра…"
        )

    def test_a_dead_copy_mark_stops_counting_and_is_swept_by_the_next_mark(
        self, redis: FakeRedis
    ) -> None:
        crawl_marks.mark_held("crawler-dead", "свободно 1 ядра…", redis=redis, now=0)
        late = crawl_marks.HELD_FRESH_SEC + 1

        assert crawl_marks.crawlers_held(redis, now=late) == crawl_marks.Held(0)
        crawl_marks.mark_held(
            "crawler-3", "свободно 5 ГБ памяти при пороге 8", redis=redis, now=late
        )
        assert set(redis.hashes[crawl_marks.CRAWL_HELD_KEY]) == {"crawler-3"}

    def test_cleared_and_broken_marks_are_not_counted(self, redis: FakeRedis) -> None:
        crawl_marks.mark_held("crawler-1", "свободно 1 ядра…", redis=redis, now=10)
        crawl_marks.clear_held("crawler-1", redis=redis)
        redis.hashes[crawl_marks.CRAWL_HELD_KEY]["crawler-broken"] = "{не json"

        assert crawl_marks.crawlers_held(redis, now=20) == crawl_marks.Held(0)

    def test_redis_down_is_not_zero_but_unknown_and_starts_are_not_held(
        self, redis: FakeRedis
    ) -> None:
        redis.down = True

        crawl_marks.mark_held("crawler-1", "свободно 1 ядра…", redis=redis)
        crawl_marks.clear_held("crawler-1", redis=redis)
        crawl_marks.mark_started(20, redis=redis)
        assert crawl_marks.crawlers_held(redis) is None
        assert crawl_marks.started_recently(redis=redis) is False

    def test_a_start_mark_lives_the_stagger(self, redis: FakeRedis) -> None:
        worker = FakeWorker(redis)

        assert load_gate.just_started(worker) is False
        load_gate.took_work(worker)

        assert redis.keys == {crawl_marks.CRAWL_STARTED_KEY: 20_000}
        assert load_gate.just_started(worker) is True

    def test_no_stagger_means_no_mark_and_no_wait(
        self, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "GATE_STAGGER_SEC", 0.0)
        redis.keys[crawl_marks.CRAWL_STARTED_KEY] = 1
        worker = FakeWorker(redis)

        load_gate.took_work(worker)

        assert load_gate.just_started(worker) is False


class _Queue:
    def __init__(self, count: int | Exception) -> None:
        self._count = count

    @property
    def count(self) -> int:
        if isinstance(self._count, Exception):
            raise self._count
        return self._count


class TestQueueHelpers:
    def test_work_is_queued_when_any_queue_has_a_job(self, redis: FakeRedis) -> None:
        worker = FakeWorker(redis)
        worker.queues = [_Queue(0), _Queue(2)]

        assert load_gate.work_queued(worker) is True

    def test_an_empty_queue_is_no_work_and_a_silent_redis_is_not_taken_for_empty(
        self, redis: FakeRedis
    ) -> None:
        worker = FakeWorker(redis)
        worker.queues = [_Queue(0)]
        assert load_gate.work_queued(worker) is False
        worker.queues = [_Queue(RedisConnectionError("нет"))]
        assert load_gate.work_queued(worker) is True

    def test_a_pause_keeps_the_copy_alive(self, redis: FakeRedis) -> None:
        worker, slept = FakeWorker(redis), []

        load_gate.pause(worker, 5.0, sleep=slept.append)

        assert (worker.beats, slept) == (1, [5.0])


class TestCrawlWorkerDequeue:
    """Копия смотрит очередь, ждёт соседний старт и машину и берёт задачу сразу после сторожа."""

    @pytest.fixture
    def events(self) -> list[Any]:
        return []

    def _worker(self, monkeypatch: pytest.MonkeyPatch) -> crawler.CrawlWorker:
        worker = crawler.CrawlWorker.__new__(crawler.CrawlWorker)
        worker._stop_requested = False
        monkeypatch.setattr(crawler.CrawlWorker, "set_state", lambda *_a, **_k: None)
        return worker

    def _wire(
        self,
        monkeypatch: pytest.MonkeyPatch,
        events: list[Any],
        *,
        queued: list[bool],
        recent: list[bool],
        got: list[Any],
    ) -> None:
        queued_it, recent_it, got_it = iter(queued), iter(recent), iter(got)

        def dequeue(_self: Any, timeout: int | None, max_idle_time: int | None = None) -> Any:
            events.append(("беру", timeout))
            return next(got_it)

        monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", dequeue)
        monkeypatch.setattr(load_gate, "work_queued", lambda _w: next(queued_it))
        monkeypatch.setattr(load_gate, "just_started", lambda _w: next(recent_it))
        monkeypatch.setattr(load_gate, "wait_for_room", lambda _w: events.append("сторож") or 0.0)
        monkeypatch.setattr(load_gate, "pause", lambda _w, s: events.append(("пауза", s)))
        monkeypatch.setattr(load_gate, "took_work", lambda _w: events.append("взяла"))

    def test_waits_for_work_then_for_a_neighbours_start_then_asks_the_gate_and_takes(
        self, monkeypatch: pytest.MonkeyPatch, events: list[Any]
    ) -> None:
        self._wire(
            monkeypatch,
            events,
            queued=[False, True, True],
            recent=[True, False],
            got=[("задача", "очередь")],
        )

        got = self._worker(monkeypatch).dequeue_job_and_maintain_ttl(405)

        poll = ("пауза", load_gate.QUEUE_POLL_SEC)
        assert got == ("задача", "очередь")
        assert events == [poll, poll, "сторож", ("беру", None), "взяла"]

    def test_a_job_taken_by_another_copy_meanwhile_is_just_another_round(
        self, monkeypatch: pytest.MonkeyPatch, events: list[Any]
    ) -> None:
        self._wire(
            monkeypatch,
            events,
            queued=[True, True],
            recent=[False, False],
            got=[None, ("задача", "очередь")],
        )

        got = self._worker(monkeypatch).dequeue_job_and_maintain_ttl(405)

        assert got == ("задача", "очередь")
        assert events == ["сторож", ("беру", None), "сторож", ("беру", None), "взяла"]

    def test_burst_mode_with_an_empty_queue_ends_at_once(
        self, monkeypatch: pytest.MonkeyPatch, events: list[Any]
    ) -> None:
        self._wire(monkeypatch, events, queued=[False], recent=[], got=[])

        assert self._worker(monkeypatch).dequeue_job_and_maintain_ttl(None) is None
        assert events == []

    def test_a_stop_request_ends_the_wait(
        self, monkeypatch: pytest.MonkeyPatch, events: list[Any]
    ) -> None:
        self._wire(monkeypatch, events, queued=[], recent=[], got=[])
        worker = self._worker(monkeypatch)
        worker._stop_requested = True

        assert worker.dequeue_job_and_maintain_ttl(405) is None
        assert events == []
