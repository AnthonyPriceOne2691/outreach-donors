"""«Провайдер не отвечает» — о поломке, а не о неответе, прошедшем сам (слово Anthony 15.10.2026).

15.10.2026 рано утром сторож прислал «Провайдер не отвечает: Ahrefs» и следом «прошло»: Ahrefs
снова ответил сам, прогонов не было. Правило (`silence.ProviderWatch`):

- временный сбой (таймаут, обрыв, 429, 5xx, страница вместо данных) — тревога, когда провайдер
  молчит 15 минут подряд и не один опрос; ответил — счёт сначала;
- отказ, который сам не пройдёт (ключ, права, адрес API, источник не настроен), — когда
  повторился на следующем опросе;
- снова замолчал вскоре после своей тревоги — тот же сбой: тревога сразу, без «прошло» посреди;
- пока молчание без тревоги, фоновый сторож спрашивает раз в 4 минуты, а не в 10;
- причина — в тексте тревоги: строка журнала пропадает с контейнером на выкатке.

Проходы — настоящий `reaper.watch` под часами теста, как в `tests/test_watch_quiet.py`: тревог
базы нет, правило — настоящее, ответы провайдеров — подставные, Telegram — список.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from backend.api.watchdog import routes
from backend.config import storage
from backend.features.ahrefs.client import AhrefsError
from backend.features.ops import alarm_feed, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import Miss, ProviderWatch
from backend.features.serp.dataforseo import SerpError
from backend.workers import reaper
from httpx import AsyncClient
from tests.conftest import TEST_DSN, bearer
from tests.test_silence_stages import MakeUser, SignIn

#: Проход сторожа и срок молчания до тревоги, секунд.
PASS = reaper.WATCHDOG_INTERVAL_SEC
DOWN = silence.PROVIDER_DOWN_MINUTES * 60
BLIP = Miss("Ahrefs", "Остаток квоты недоступен: Ahrefs ответил 503: Service Unavailable")
REVOKED = Miss(
    "Ahrefs", "Остаток квоты недоступен: Ahrefs ответил 401: invalid api key", permanent=True
)
TAIL = (
    "Прогон при недоступном остатке не запускается вовсе — узнать об этом лучше здесь, "
    "чем отказом посреди оплаченной работы"
)


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


# --- правило ---------------------------------------------------------------------------------


def test_one_miss_is_no_alarm_and_an_answer_starts_the_count_over() -> None:
    clock = Clock()
    watch = ProviderWatch(clock=clock)

    first = watch.judge([BLIP])
    doubtful = watch.doubtful
    clock.now = 240.0
    answered = watch.judge([])
    calm = watch.doubtful
    clock.now = 240.0 + DOWN
    again = watch.judge([BLIP])

    assert (first, doubtful) == (None, True)
    assert (answered, calm) == (None, False)
    assert again is None, "молчание после ответа считается с начала, а не с первого неответа"


def test_silence_held_for_fifteen_minutes_is_an_alarm_with_the_reason() -> None:
    clock = Clock()
    watch = ProviderWatch(clock=clock)
    early = []
    for moment in range(0, DOWN, 240):
        clock.now = float(moment)
        early.append(watch.judge([BLIP]))
    clock.now = float(DOWN)

    alarm = watch.judge([BLIP])

    assert early == [None] * len(early)
    assert alarm is not None
    assert (alarm.code, alarm.title) == (silence.PROVIDERS_ALARM, "Провайдер не отвечает")
    assert alarm.detail == f"Не отвечает: Ahrefs — 15 мин подряд ({BLIP.reason}). {TAIL}"
    assert not watch.doubtful, "тревога есть — опрос прежним порядком"


def test_two_misses_with_no_answer_between_are_enough_however_far_apart() -> None:
    """Экран спрашивает, только когда «Обзор» открыт: два неответа с разницей в 15 минут и без
    ответа между ними — те же 15 минут молчания."""
    clock = Clock()
    watch = ProviderWatch(clock=clock)

    assert watch.judge([BLIP]) is None
    clock.now = float(DOWN)
    assert watch.judge([BLIP]) is not None


def test_a_single_miss_is_no_alarm_however_long_since_the_last_answer() -> None:
    assert ProviderWatch(clock=Clock(now=10.0 * DOWN)).judge([BLIP]) is None


def test_a_refusal_that_will_not_pass_is_an_alarm_when_it_repeats() -> None:
    clock = Clock()
    watch = ProviderWatch(clock=clock)

    first = watch.judge([REVOKED])
    clock.now = reaper.CONFIRM_INTERVAL_SEC
    alarm = watch.judge([REVOKED])

    assert first is None, "один такой ответ бывает и у посредника перед API"
    assert alarm is not None
    assert alarm.detail == (
        f"Не отвечает: Ahrefs — отказ, который сам не пройдёт ({REVOKED.reason}). {TAIL}"
    )


def test_silence_soon_after_an_alarm_is_the_same_failure() -> None:
    clock = Clock()
    watch = ProviderWatch(clock=clock)
    watch.judge([BLIP])
    clock.now = float(DOWN)
    alarmed = watch.judge([BLIP])
    clock.now += reaper.PROVIDERS_INTERVAL_SEC
    answered = watch.judge([])
    clock.now += reaper.PROVIDERS_INTERVAL_SEC

    relapse = watch.judge([BLIP])

    assert alarmed is not None
    assert answered is None
    assert relapse is not None
    assert relapse.detail == (
        f"Не отвечает: Ahrefs — снова, вскоре после тревоги ({BLIP.reason}). {TAIL}"
    )


def test_silence_long_after_an_alarm_waits_its_fifteen_minutes_again() -> None:
    clock = Clock()
    watch = ProviderWatch(clock=clock)
    watch.judge([BLIP])
    clock.now = float(DOWN)
    assert watch.judge([BLIP]) is not None
    alarmed = clock.now
    clock.now += reaper.PROVIDERS_INTERVAL_SEC
    watch.judge([])
    clock.now = alarmed + silence.PROVIDER_RELAPSE_MINUTES * 60

    assert watch.judge([BLIP]) is None


def test_each_provider_keeps_its_own_count() -> None:
    serp = Miss(silence.SERP, "баланс: 500")
    clock = Clock()
    watch = ProviderWatch(clock=clock)
    watch.judge([BLIP])
    clock.now = float(DOWN)

    alarm = watch.judge([BLIP, serp])

    assert alarm is not None
    assert alarm.detail.startswith("Не отвечает: Ahrefs — 15 мин подряд (")
    assert silence.SERP not in alarm.detail
    assert watch.doubtful, "источник выдачи молчит без тревоги — проверить раньше"


def test_the_rule_fits_the_watchdog_cadence() -> None:
    """Опрос раз в 4 минуты — не чаще прохода через один: медленный опрос попадает в окно тревоги
    почты не больше одного раза (`tests/test_mail_watch_retired.py`). Молчание подтверждают
    несколько опросов. «Тот же сбой» помнится дольше, чем лента говорит «прошло» после последнего
    опроса с тревогой: иначе мигание давало бы «прошло» посреди нового молчания."""
    assert reaper.CONFIRM_INTERVAL_SEC >= 2 * reaper.WATCHDOG_INTERVAL_SEC
    assert reaper.CONFIRM_INTERVAL_SEC < reaper.PROVIDERS_INTERVAL_SEC
    assert DOWN >= 3 * reaper.CONFIRM_INTERVAL_SEC
    assert silence.PROVIDER_RELAPSE_MINUTES * 60 >= reaper.QUIET_SEC + reaper.PROVIDERS_INTERVAL_SEC


# --- опрос: причина и пройдёт ли само ------------------------------------------------------------


class _Ahrefs:
    """Ahrefs, который отвечает об остатке — или отказывает `error`."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    async def limits_and_usage(self) -> dict[str, object]:
        if self.error is not None:
            raise self.error
        return {}

    async def aclose(self) -> None:
        return None


def _ahrefs(error: BaseException | None = None) -> Callable[[], _Ahrefs]:
    return lambda: _Ahrefs(error)


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (AhrefsError("Остаток квоты недоступен: Ahrefs ответил 503: Service Unavailable"), False),
        (AhrefsError("Остаток квоты недоступен: ReadTimeout"), False),
        (
            AhrefsError("Остаток квоты недоступен: Ahrefs ответил 401: invalid", permanent=True),
            True,
        ),
        (OSError("Temporary failure in name resolution"), False),
    ],
)
async def test_an_ahrefs_miss_carries_its_reason_and_whether_it_passes(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, permanent: bool
) -> None:
    async def serp_answers() -> None:
        return None

    monkeypatch.setattr(silence, "AhrefsClient", _ahrefs(error))
    monkeypatch.setattr(silence, "_serp_silent", serp_answers)

    assert await silence.ask_providers() == [Miss("Ahrefs", str(error), permanent=permanent)]


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (SerpError("баланс: 500"), False),
        (SerpError("ключ отклонён", permanent=True), True),
        (OSError("Connection reset by peer"), False),
    ],
)
async def test_a_serp_miss_carries_its_reason_and_whether_it_passes(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, permanent: bool
) -> None:
    class Serp:
        async def balance(self) -> None:
            raise error

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(silence, "AhrefsClient", _ahrefs())
    monkeypatch.setattr(silence, "build_provider", lambda _ahrefs: Serp())

    assert await silence.ask_providers() == [Miss(silence.SERP, str(error), permanent=permanent)]


def test_the_reason_is_one_short_line() -> None:
    reason = silence._reason(AhrefsError("Ahrefs ответил 502:\n<html>\n" + "x" * 300))

    assert reason.startswith("Ahrefs ответил 502: <html> x")
    assert len(reason) == 160


# --- проходы сторожа ---------------------------------------------------------------------------


@dataclass
class World:
    """Мир прохода: часы, ответы провайдеров; когда их спросили и что ушло в Telegram."""

    now: float = 0.0
    misses: list[Miss] = field(default_factory=list)
    asked: list[float] = field(default_factory=list)
    said: list[tuple[float, str]] = field(default_factory=list)

    async def until(self, end: float, *misses: Miss) -> None:
        """Проходы раз в `PASS` от текущей минуты до `end` включительно с этими неответами."""
        self.misses = list(misses)
        while self.now <= end:
            await reaper.watch()
            self.now += PASS


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    """Свои часы у ленты, правила, опроса и журнала сторожа — настройки как у процесса."""
    state = World()

    def tick() -> float:
        return state.now

    async def alarms(_session: object, *, stages: object) -> list[Alarm]:
        return []

    async def ask() -> list[Miss]:
        state.asked.append(state.now)
        return list(state.misses)

    async def telegram(text: str) -> bool:
        state.said.append((state.now, text))
        return True

    monkeypatch.setattr(storage, "DSN", TEST_DSN)  # сессия прохода не открывает соединения
    monkeypatch.setattr(silence, "alarms", alarms)
    monkeypatch.setattr(silence, "ask_providers", ask)
    monkeypatch.setattr(silence, "WATCH", ProviderWatch(clock=tick))
    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = Feed(reaper.FEED.name, quiet_sec=reaper.FEED.quiet_sec, clock=tick)
    monkeypatch.setattr(reaper, "FEED", feed)
    monkeypatch.setattr(reaper, "PROVIDERS", reaper.Providers(clock=tick))
    monkeypatch.setattr(reaper, "JOURNAL", silence.Journal(every=reaper.JOURNAL.every, clock=tick))
    return state


async def test_a_miss_that_passed_by_itself_says_nothing(world: World) -> None:
    """Утро 15.10.2026: один неответ — ни «тревога», ни «прошло»; проверка — через 4 минуты."""
    await world.until(0, BLIP)
    await world.until(3 * reaper.PROVIDERS_INTERVAL_SEC)

    assert world.said == []
    assert world.asked[:3] == [
        0.0,
        reaper.CONFIRM_INTERVAL_SEC,
        reaper.CONFIRM_INTERVAL_SEC + reaper.PROVIDERS_INTERVAL_SEC,
    ]


async def test_silence_held_for_fifteen_minutes_is_said_once_and_then_over(world: World) -> None:
    await world.until(DOWN + 2 * PASS, BLIP)
    confirmed = next(moment for moment in world.asked if moment >= DOWN)
    minutes = int(confirmed // 60)
    await world.until(confirmed + 3 * reaper.QUIET_SEC)

    assert world.said == [
        (
            confirmed,
            f"тревога: Провайдер не отвечает. Не отвечает: Ahrefs — {minutes} мин подряд "
            f"({BLIP.reason}). {TAIL}",
        ),
        (confirmed + reaper.QUIET_SEC, "прошло: Провайдер не отвечает"),
    ]


async def test_a_provider_answering_once_amid_its_silence_is_one_alarm(world: World) -> None:
    """Ответил одному опросу и снова молчит — тот же сбой: ни «прошло», ни второй «тревоги»."""
    await world.until(DOWN + 2 * PASS, BLIP)
    answered = next(moment for moment in world.asked if moment >= DOWN)
    answered += reaper.PROVIDERS_INTERVAL_SEC
    await world.until(answered)
    await world.until(answered + 3 * reaper.QUIET_SEC, BLIP)

    assert [text.split(".")[0] for _, text in world.said] == ["тревога: Провайдер не отвечает"]


async def test_a_revoked_key_is_said_on_the_next_probe(world: World) -> None:
    await world.until(2 * reaper.CONFIRM_INTERVAL_SEC, REVOKED)

    assert world.asked == [0.0, reaper.CONFIRM_INTERVAL_SEC], "после тревоги — прежним порядком"
    assert [moment for moment, _ in world.said] == [reaper.CONFIRM_INTERVAL_SEC]
    assert "отказ, который сам не пройдёт" in world.said[0][1]


# --- «Обзор» -----------------------------------------------------------------------------------


async def test_the_overview_shows_the_alarm_only_once_the_silence_holds(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user("viewer@provider-watch.example.test")
    headers = bearer(await sign_in("viewer@provider-watch.example.test"))
    clock = Clock()

    async def ask() -> list[Miss]:
        return [BLIP]

    monkeypatch.setattr(silence, "ask_providers", ask)
    monkeypatch.setattr(silence, "WATCH", ProviderWatch(clock=clock))
    monkeypatch.setattr(routes, "PROBES", routes.Probes(clock=clock))

    first = (await client.get("/api/watchdog", headers=headers)).json()["alarms"]
    clock.now = float(DOWN)
    held = (await client.get("/api/watchdog", headers=headers)).json()["alarms"]

    assert silence.PROVIDERS_ALARM not in {card["code"] for card in first}
    assert held[0]["code"] == silence.PROVIDERS_ALARM
    assert held[0]["detail"].startswith("Не отвечает: Ahrefs — 15 мин подряд (")
