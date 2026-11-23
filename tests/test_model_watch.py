"""Сторож модели (23.11.2026): нет денег — «пополните счёт OpenAI»; модель отказывает подряд.

В 07:03 МСК на счёте OpenAI кончились деньги, а сервис не сказал: отказ видел только процесс, где он
случился, и тревоги о модели не было. Здесь держится:

- **след из процесса:** каждый вызов модели (`llm.post_chat`) пишет в общий след (`llm_marks`)
  удачу, пустой счёт или отказ; частота (429 без «нет кредитов») — ничего;
- **нет денег:** тревога с последнего отказа «нет кредитов», пока после него не ответила ни одна
  модель; тишина её не снимает; «прошло» — по удаче и сроку тишины ленты;
- **отказывает подряд:** десять отказов одной модели после её удачи и за десять минут — тревога с
  моделью и последним отказом; удача той же модели её снимает, чужая — нет; без новых отказов
  дольше часа — проходит;
- **без Redis:** вызов модели идёт и говорит об этом строкой не чаще раза в минуту; сторож — с
  прежним ответом; битые записи следа — пропуском, а не падением;
- **экран:** «Обзор» показывает тревогу модели — Telegram зовёт туда.

Провайдер — подставной транспорт; Redis — хранилище в памяти (`conftest.MarkStore`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from backend.features.core.domain import UserRole
from backend.features.ops import alarm_feed, model_watch, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.model_watch import BROKE, EMPTY, HOLD_SEC, REFUSING, STUCK
from backend.shared import llm_marks
from backend.shared.llm import post_chat
from backend.shared.llm_marks import ANSWERED_KEY, REFUSED_KEY, Refused
from httpx import AsyncClient
from tests.conftest import WATCH_MINUTE, MarkStore, bearer
from tests.test_alarm_concern import _plain
from tests.test_model_no_credits import (
    ANSWER,
    KEY,
    MODEL,
    RATE_BODY,
    REFUSALS,
    Provider,
    Reply,
    answered,
    no_credits,
)
from tests.test_silence_stages import MakeUser, SignIn

#: Минута ленты в тестах — 03:11 МСК (`conftest.WATCH_MINUTE`): след на тех же часах.
T0 = WATCH_MINUTE.timestamp()
#: Слова провайдера о пустом счёте — как 23.11.2026, без признака в теле.
EMPTY_WORDS = "HTTP 429: You have no credits remaining. Add credits to continue using the API."
GONE = "HTTP 404: The model does not exist or you do not have access to it."


@pytest.fixture
def moment(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Часы следа: секунды эпохи, двигает тест."""
    now = [T0]
    monkeypatch.setattr(llm_marks, "clock", lambda: now[0])
    return now


async def _call(reply: Callable[[], Reply], *, key: str = KEY, model: str = MODEL) -> Any:
    """Один вызов модели через общий `post_chat` — с подставным провайдером."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(Provider(reply))) as http:
        return await post_chat(http, api_key=key, payload={"model": model}, topic="тест")


async def _refusals(moment: list[float], *, at: list[float], model: str = "gpt-5") -> None:
    """Отказы модели `model` в минуты `at` — так, как их пишет `post_chat`."""
    for when in at:
        moment[0] = when
        await llm_marks.refused(model, GONE)


async def _alarms(now: float = T0) -> dict[str, Any]:
    return {alarm.code: alarm for alarm in await model_watch.alarms(now)}


# --- след из процесса ------------------------------------------------------------------------


async def test_each_outcome_of_a_model_call_leaves_its_trace(moment: list[float]) -> None:
    for when, reply in [
        (T0 - 300, answered),
        (T0 - 200, no_credits),
        (T0 - 100, REFUSALS["ключ 401"][0]),
        (T0, lambda: httpx.Response(429, json=RATE_BODY)),  # частота — не в след
    ]:
        moment[0] = when
        await _call(reply)
    moment[0] = T0 + 10
    await _call(answered, key="")  # ключ не задан — отказ «запрос», и тоже в след

    trace = await llm_marks.read()

    assert trace is not None
    assert trace.answered == {MODEL: T0 - 300}
    assert trace.broke_at == T0 - 200
    assert "You have no credits remaining" in trace.broke_why
    assert [(one.at, one.model) for one in trace.refused] == [(T0 + 10, MODEL), (T0 - 100, MODEL)]
    assert trace.refused[0].why.startswith("LLM_API_KEY не задан")
    assert trace.refused[1].why == "HTTP 401: Incorrect API key provided"


async def test_rate_limits_and_the_empty_account_are_not_refusals_in_a_row(
    moment: list[float],
) -> None:
    for _ in range(model_watch.STREAK):
        await _call(lambda: httpx.Response(429, json=RATE_BODY))
        await _call(no_credits)

    trace = await llm_marks.read()

    assert trace is not None
    assert trace.refused == ()
    assert set(await _alarms()) == {BROKE}


# --- нет денег ------------------------------------------------------------------------------


async def test_the_empty_account_is_an_alarm_until_any_model_answers_again(
    moment: list[float],
) -> None:
    moment[0] = T0 - 120
    await llm_marks.answered("gpt-6.1-terra")  # удача до пустого счёта его не снимает
    moment[0] = T0 - 60
    await llm_marks.broke(MODEL, EMPTY_WORDS)

    found = await _alarms()

    alarm = found[BROKE]
    assert alarm.title == "У модели нет денег — пополните счёт OpenAI"
    assert alarm.detail.startswith("OpenAI отвечает «нет кредитов», последний раз — в 03:10 МСК")
    assert f"(ответ OpenAI: «{EMPTY_WORDS}»)" in alarm.detail
    assert "раздел Billing" in alarm.detail
    assert alarm.concern == EMPTY
    _plain(EMPTY)

    moment[0] = T0 + 30
    await llm_marks.answered("gpt-6.1-terra")  # счёт один на все модели ключа

    assert await _alarms(T0 + 60) == {}


async def test_silence_after_the_empty_account_is_not_over(moment: list[float]) -> None:
    """После пустого счёта модель зовут редко или никто: тишина — «некому спросить», а не
    «пополнили»."""
    await llm_marks.broke(MODEL, EMPTY_WORDS)

    assert set(await _alarms(T0 + 6 * 3600)) == {BROKE}


async def test_the_watchdog_pass_says_it_once_and_over_after_an_answer(
    moment: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проход сторожа тишины (`silence.with_providers`, процесс разбора) несёт тревогу модели в
    ленту: одно «тревога», «прошло» — после удачи и срока тишины ленты."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    async def everyone_answers() -> None:
        return None

    ticks = [0.0]
    feed = Feed("model-watch-test", quiet_sec=1200.0, clock=lambda: ticks[0], screen="Обзор")
    monkeypatch.setattr(alarm_feed, "send_alert", send)
    await llm_marks.broke(MODEL, EMPTY_WORDS)

    for tick in (0.0, 120.0):
        ticks[0] = tick
        await feed.tell(await silence.with_providers([], probe=everyone_answers))
    moment[0] = T0 + 200
    await llm_marks.answered(MODEL)
    for tick in (240.0, 1440.0):
        ticks[0] = tick
        await feed.tell(await silence.with_providers([], probe=everyone_answers))

    assert len(said) == 2, said
    alarmed, over = said
    assert alarmed.startswith("тревога: У модели нет денег — пополните счёт OpenAI. С 03:11 МСК.\n")
    assert "\nГде смотреть: экран «Обзор».\n" in alarmed
    assert alarmed.endswith(f"\n{EMPTY.words}")
    assert over.startswith("прошло: У модели нет денег — пополните счёт OpenAI")


# --- отказывает подряд ----------------------------------------------------------------------


async def test_ten_refusals_in_a_row_within_ten_minutes_are_an_alarm(moment: list[float]) -> None:
    await _refusals(moment, at=[T0 - 540 + 60 * step for step in range(10)])

    alarm = (await _alarms())[REFUSING]

    assert alarm.title == "Модель отказывает подряд"
    assert alarm.detail.startswith(f"gpt-5 — 10 отказов подряд с 03:02 МСК, последний: «{GONE}». ")
    assert "Частота и «нет кредитов» сюда не входят" in alarm.detail
    assert alarm.concern == STUCK
    _plain(STUCK)


@pytest.mark.parametrize(
    "at",
    [
        [T0 - 480 + 60 * step for step in range(9)],
        [T0 - 630 + 70 * step for step in range(10)],
    ],
    ids=["nine", "ten-in-eleven-minutes"],
)
async def test_fewer_or_slower_refusals_are_no_alarm(moment: list[float], at: list[float]) -> None:
    await _refusals(moment, at=at)

    assert await _alarms() == {}


async def test_an_answer_of_the_same_model_ends_the_streak_and_of_another_does_not(
    moment: list[float],
) -> None:
    await _refusals(moment, at=[T0 - 540 + 60 * step for step in range(10)])
    moment[0] = T0 + 1
    await llm_marks.answered("gpt-6.1-terra")  # судья на другой модели снятую не прячет

    assert set(await _alarms(T0 + 2)) == {REFUSING}

    await llm_marks.answered("gpt-5")

    assert await _alarms(T0 + 2) == {}


async def test_each_model_counts_its_own_streak(moment: list[float]) -> None:
    """Пять отказов одной и пять другой — не десять подряд: у каждой модели свой счёт. Две серии —
    строкой на модель."""
    await _refusals(moment, at=[T0 - 500 + 50 * step for step in range(5)], model="gpt-5")
    await _refusals(moment, at=[T0 - 240 + 50 * step for step in range(5)], model="gpt-5-mini")

    assert await _alarms() == {}

    await _refusals(moment, at=[T0 - 10 + step for step in range(5)], model="gpt-5")
    await _refusals(moment, at=[T0 - 4 + step for step in range(5)], model="gpt-5-mini")
    detail = (await _alarms(T0 + 1))[REFUSING].detail

    assert detail.startswith(f"gpt-5 — 10 отказов подряд с 03:02 МСК, последний: «{GONE}»; ")
    assert "; gpt-5-mini — 10 отказов подряд с 03:07 МСК, последний: «" in detail


async def test_the_streak_passes_when_the_model_is_no_longer_called(moment: list[float]) -> None:
    await _refusals(moment, at=[T0 - 540 + 60 * step for step in range(10)])

    assert set(await _alarms(T0 + HOLD_SEC)) == {REFUSING}
    assert await _alarms(T0 + HOLD_SEC + 1) == {}


async def test_one_more_refusal_after_a_long_pause_brings_the_streak_back(
    moment: list[float],
) -> None:
    """Удачи так и не было — серия часовой давности и новый отказ снова тревога: модель всё так же
    не отвечает."""
    await _refusals(moment, at=[T0 - 540 + 60 * step for step in range(10)])
    await _refusals(moment, at=[T0 + 2 * HOLD_SEC])

    detail = (await _alarms(T0 + 2 * HOLD_SEC))[REFUSING].detail

    assert detail.startswith("gpt-5 — 11 отказов подряд с 03:02 МСК, последний: ")


# --- без Redis ------------------------------------------------------------------------------


async def test_a_model_call_goes_on_without_redis_and_says_so_once_a_minute(
    model_marks: MarkStore, caplog: pytest.LogCaptureFixture
) -> None:
    model_marks.down = True

    with caplog.at_level(logging.WARNING, logger=llm_marks.__name__):
        first, second = await _call(answered), await _call(no_credits)

    assert first == ANSWER
    assert second.no_credits
    [line] = [one for one in caplog.records if one.name == llm_marks.__name__]
    assert line.getMessage().startswith("след модели: Redis не ответил — удача модели не записана")


async def test_the_watch_keeps_its_last_answer_while_redis_is_silent(
    model_marks: MarkStore, moment: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """Молчание Redis — не «прошло»: сторож отдаёт прежний ответ своего процесса; ответа ещё не было
    — тревог нет, а не падение прохода."""
    await llm_marks.broke(MODEL, EMPTY_WORDS)
    assert set(await _alarms()) == {BROKE}
    model_marks.down = True

    with caplog.at_level(logging.WARNING, logger=llm_marks.__name__):
        held = await _alarms()
    model_watch.LAST.alarms = []

    assert set(held) == {BROKE}
    assert "след модели не прочитан" in caplog.text
    assert await _alarms() == {}


async def test_broken_records_of_the_trace_are_skipped(
    model_marks: MarkStore, caplog: pytest.LogCaptureFixture
) -> None:
    model_marks.hashes[ANSWERED_KEY] = {"gpt-5": "никогда", MODEL: str(T0)}
    model_marks.lists[REFUSED_KEY] = [
        "не JSON",
        '{"at": "вчера", "model": "gpt-5", "why": "?"}',
        f'{{"at": {T0}, "model": "gpt-5", "why": "{GONE}"}}',
    ]

    with caplog.at_level(logging.WARNING, logger=llm_marks.__name__):
        trace = await llm_marks.read()

    assert trace is not None
    assert trace.answered == {MODEL: T0}
    assert trace.refused == (Refused(at=T0, model="gpt-5", why=GONE),)
    assert (trace.broke_at, trace.broke_why) == (None, "")
    assert caplog.text.count("след модели:") == 3


# --- экран ----------------------------------------------------------------------------------


async def test_the_overview_shows_the_alarm_of_the_model(
    make_user: MakeUser,
    sign_in: SignIn,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram зовёт на «Обзор» — там тревога модели и видна: после транзакции запроса, как опрос
    провайдеров."""

    async def everyone_answers() -> None:
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", everyone_answers)
    await llm_marks.broke(MODEL, EMPTY_WORDS)
    await make_user("owner@model-watch.example.test", role=UserRole.OPERATOR)
    headers = bearer(await sign_in("owner@model-watch.example.test"))

    response = await client.get("/api/watchdog", headers=headers)

    assert response.status_code == 200, response.text
    [card] = response.json()["alarms"]
    assert (card["code"], card["concern"]["words"]) == (BROKE, EMPTY.words)
