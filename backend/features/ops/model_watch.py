"""Сторож модели: на счёте OpenAI нет денег — «пополните счёт»; модель отказывает подряд.

23.11.2026 в 07:03 МСК на счёте OpenAI кончились деньги, и сервис промолчал: отказ «нет кредитов»
видели только процессы, где он случился (строкой журнала и словами своего экрана), а тревоги о
модели не было вовсе — у счёта выдачи, Hunter и ключа Ahrefs они есть давно. Теперь каждый вызов
модели оставляет след в Redis (`shared/llm_marks.py`), а этот сторож превращает след в тревоги
ленты: в проходе сторожа тишины, в его сетевой части (`silence.with_providers`, процесс разбора), и
на «Обзоре» (`api/watchdog`). Своего прохода ему не нужно: чтение следа — три запроса к Redis.

**Нет денег** (`BROKE`) — с последнего отказа «нет кредитов», пока после него модель ни разу не
ответила. «Прошло» — только по удаче, а не по тишине: пустой счёт останавливает работу с моделью
(судья встаёт, прогон не начинается), и после первых отказов модель зовут редко или никто, — тишина
значит «некому спросить», а не «пополнили». Удача — любой модели: счёт один на все модели ключа.
Пополнили — первый удачный вызов («Продолжить», разбор пришедшего ответа, проверка перед прогоном)
снимает тревогу, а «прошло» лента скажет по своему сроку тишины (`workers/reaper.QUIET_SEC`).

**Отказывает подряд** (`REFUSING`) — у модели не меньше `STREAK` отказов после её последней удачи, и
`STREAK` подряд уложились в `WINDOW_SEC`: ключ не принят, модель снята или переименована, запрос не
собран, провайдер не отвечает. Частота (429 без «нет кредитов») и пустой счёт сюда не входят: первая
проходит сама, у второго своя тревога. Подряд — у каждой модели своё: снятая модель писем не
прячется за удачами судьи на другой модели, а единичные отказы среди удач (страница, которую модель
не взялась читать) тревогой не становятся. Держится так же — до удачи этой модели; без новых отказов
дольше `HOLD_SEC` — проходит: модель больше не зовут, её сменили в настройках.

**Redis не ответил** — прежний ответ этого процесса (`LAST`): молчание Redis — не «прошло».
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.features.ops.alarm_words import clock, counted
from backend.features.ops.alarms import Alarm, cause
from backend.shared import llm_marks
from backend.shared.llm_marks import Refused, Trace

#: Коды тревог: лента помнит сказанное под ними, числа в словах её не будят.
BROKE = "model-no-credits"
REFUSING = "model-refusing"

#: Сколько отказов подряд у модели — тревога, и в какое окно они должны уложиться: десять отказов
#: за десять минут (у временных — по три попытки каждый, `llm.ATTEMPTS`) — уже не случайность.
STREAK = 10
WINDOW_SEC = 600.0
#: Сколько «отказывает подряд» держится без новых отказов, если удачи так и не было.
HOLD_SEC = 3600.0
#: Сколько знаков ответа провайдера — в словах тревоги.
WHY_CHARS = 160

#: Формы слова: «12 отказов».
REFUSALS = ("отказ", "отказа", "отказов")

#: Пометка пустого счёта: работа с моделью стоит, пока его не пополнят.
EMPTY = cause(
    "судья, письма и разбор ответов стоят, прогоны не начинаются", "пополнить счёт OpenAI"
)
#: Пометка отказов подряд: вызовы не проходят и сами не пройдут.
STUCK = cause(
    "вызовы модели не проходят, и работа с ней стоит",
    "по последнему отказу проверить ключ модели, её название и доступность OpenAI",
)


@dataclass
class Last:
    """Ответ сторожа с прошлого чтения следа — для прохода, где Redis не ответил."""

    alarms: list[Alarm] = field(default_factory=list)


#: Прежний ответ этого процесса; тесты начинают с пустого (`tests/conftest.py`).
LAST = Last()


async def alarms(now: float | None = None) -> list[Alarm]:
    """Тревоги модели по следу: нет денег и отказывает подряд. Redis не ответил — прежний ответ
    (строку журнала пишет `llm_marks`). `now` — секунды эпохи; по умолчанию — часы следа."""
    trace = await llm_marks.read()
    if trace is None:
        return list(LAST.alarms)
    moment = llm_marks.clock() if now is None else now
    found = [broke(trace, moment), refusing(trace, moment)]
    LAST.alarms = [alarm for alarm in found if alarm is not None]
    return list(LAST.alarms)


def broke(trace: Trace, now: float) -> Alarm | None:
    """«У модели нет денег» — с последнего отказа «нет кредитов», пока после него не было удачи."""
    last = trace.last_answer
    if trace.broke_at is None or (last is not None and last > trace.broke_at):
        return None
    return Alarm(
        code=BROKE,
        title="У модели нет денег — пополните счёт OpenAI",
        detail=(
            f"OpenAI отвечает «нет кредитов», последний раз — в {_clock(trace.broke_at, now)} МСК: "
            f"на счёте кончились деньги (ответ OpenAI: «{trace.broke_why[:WHY_CHARS]}»). Пока его "
            "не пополнят, судья и письма стоят, ответы не разбираются, прогоны не начинаются. "
            "Пополнить — в кабинете OpenAI, раздел Billing; тревога пройдёт с первым удачным "
            "вызовом модели"
        ),
        concern=EMPTY,
    )


def refusing(trace: Trace, now: float) -> Alarm | None:
    """«Модель отказывает подряд» — по строке на модель с серией (`_stuck`); серий нет — `None`."""
    lines = [_line(model, found, now) for model, found in _streaks(trace).items()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return Alarm(
        code=REFUSING,
        title="Модель отказывает подряд",
        detail=(
            f"{'; '.join(lines)}. Частота и «нет кредитов» сюда не входят: эти вызовы не прошли "
            "вовсе. Тревога пройдёт, когда модель снова ответит"
        ),
        concern=STUCK,
    )


def _streaks(trace: Trace) -> dict[str, list[Refused]]:
    """Отказы каждой модели после её последней удачи, старые первыми."""
    found: dict[str, list[Refused]] = {}
    for one in sorted(trace.refused, key=lambda refusal: refusal.at):
        if one.at > trace.answered.get(one.model, -math.inf):
            found.setdefault(one.model, []).append(one)
    return found


def _stuck(found: Sequence[Refused], now: float) -> bool:
    """Серия: последний отказ не старше `HOLD_SEC`, и `STREAK` подряд уложились в `WINDOW_SEC`."""
    if len(found) < STREAK or now - found[-1].at > HOLD_SEC:
        return False
    return any(
        found[end].at - found[end - STREAK + 1].at <= WINDOW_SEC
        for end in range(STREAK - 1, len(found))
    )


def _line(model: str, found: Sequence[Refused], now: float) -> str:
    """«gpt-5-mini — 12 отказов подряд с 03:05 МСК, последний: «HTTP 401: …»»; не серия — пусто."""
    if not _stuck(found, now):
        return ""
    return (
        f"{model or 'модель без названия'} — {counted(len(found), REFUSALS)} подряд с "
        f"{_clock(found[0].at, now)} МСК, последний: «{found[-1].why[:WHY_CHARS]}»"
    )


def _clock(at: float, now: float) -> str:
    """Минута по Москве, не сегодня — с датой (`alarm_words.clock`)."""
    return clock(datetime.fromtimestamp(at, UTC), datetime.fromtimestamp(now, UTC))
