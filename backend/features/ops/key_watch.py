"""Сторож ключа Ahrefs: сколько выбрано от капа и снимок ключа из опроса провайдеров.

**Предупреждение, тревога, выбран** (слово владельца 16.10.2026: «предупреждала, когда лимит
выбран на сколько-то процентов»). Доля — по тому, во что упрёмся раньше: остаток — меньший
из остатков нашего капа, лимита ключа и лимита пространства, от действующего капа
(`runs/ahrefs_cap.KeyCap.used_percent`). С `WARN_PERCENT` — предупреждение, с `ALARM_PERCENT`
— тревога, весь — «кап выбран» (`cap-reached`, прежний код). В словах — сколько осталось,
из скольких, во что упирается и когда обнуление ключа. Пороги — здесь, одним местом, и
настройкой не становятся.

**Уровень — в коде тревоги, а не в словах.** Лента тревог (`ops/alarm_feed.Feed`) решает
«сказать» по коду: новый код — сообщение, пропавший — «прошло». Числа в словах меняются
с каждым проходом, и при одном коде на все уровни лента молчала бы о переходе 80 → 95; с
уровнем в коде переход говорится сразу, а «прошло» о прежнем уровне — когда выйдет его
тишина. Тревога одна за проход — самого высокого уровня.

**Повод для беспокойства — по уровню** (слово Anthony 29.10.2026). 80 % — повода нет: прогоны
запускаются, это предупреждение заранее. 95 % — есть: следующий прогон может не уложиться в
остаток. Выбран — есть: новые прогоны стоят до обнуления. Уровень в коде — и пометка говорится
вместе с ним.

**Снимок ключа из опроса сторожа** (`heard`, `keep_heard`). Провайдеров сторож спрашивает
без сессии (`silence.with_providers`): соединение не должно ждать чужих ответов. Ответ
Ahrefs запоминается здесь, а в снимок его кладёт тот, у кого сессия: проход сторожа — после
опроса, экран сторожа — после своей транзакции. Свежим снимок держит именно сторож: его опрос
— раз в десять минут (`workers/reaper.PROVIDERS_INTERVAL_SEC`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.units import Quota
from backend.features.ops.alarms import Alarm, Concern, cause, no_cause
from backend.features.runs.ahrefs_cap import KeyCap, key_cap
from backend.features.runs.ahrefs_key import day_words, remember
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: С какой доли капа — предупреждение и с какой — тревога, целыми процентами.
WARN_PERCENT = 80
ALARM_PERCENT = 95

#: Коды по уровням. «Кап выбран» — прежний код: лента помнит сказанное под ним.
WARN_CODE = "ahrefs-key-80"
ALARM_CODE = "ahrefs-key-95"
REACHED_CODE = "cap-reached"


@dataclass
class Heard:
    """Последний ответ Ahrefs об остатке в этом процессе — до записи в снимок ключа."""

    quota: Quota | None = None
    at: datetime | None = None


#: Услышанное опросом этого процесса; тесты начинают с пустого (`tests/conftest.py`).
HEARD = Heard()


def heard(answer: object) -> None:
    """Запомнить ответ опроса провайдеров: снимком ключа его положит тот, у кого сессия."""
    if not isinstance(answer, Mapping):
        logger.warning(
            "сторож ключа: ответ Ahrefs об остатке — не словарь (%s), снимок не обновлён",
            type(answer).__name__,
        )
        return
    HEARD.quota, HEARD.at = Quota.from_payload(answer), datetime.now(UTC)


async def keep_heard(session: AsyncSession) -> bool:
    """Услышанный ответ — в снимок ключа транзакцией `session`; фиксирует вызывающий.
    `False` — класть нечего: опрос Ahrefs не спрашивал или ответ уже положен."""
    quota, at = HEARD.quota, HEARD.at
    if quota is None or at is None:
        return False
    HEARD.quota = HEARD.at = None
    return await remember(session, quota, at=at)


def _until(cap: KeyCap) -> str:
    """До какого дня — словами: обнуление ключа, а без его даты — первое число по календарю."""
    day = day_words(cap.cycle.next_reset)
    if cap.cycle.by_key:
        return f"обнуление ключа {day}"
    return f"цикл по календарю — до {day}: Ahrefs не назвал даты обнуления"


def _binding(cap: KeyCap) -> str:
    """Во что упирается — словами и с числами: наш кап, лимит ключа или пространства."""
    quota = None if cap.key is None else cap.key.quota
    if cap.binding == "key" and quota is not None and quota.key_limit is not None:
        return (
            f"в лимит ключа: на нём выбрано {spaced(quota.key_used or 0)} из "
            f"{spaced(quota.key_limit)} вместе с соседней системой на общем ключе"
        )
    if cap.binding == "workspace" and quota is not None and quota.workspace_limit is not None:
        return (
            f"в лимит пространства Ahrefs: выбрано {spaced(quota.workspace_used or 0)} из "
            f"{spaced(quota.workspace_limit)} всеми его ключами"
        )
    return f"в наш кап: потрачено нами {spaced(cap.spent)} за цикл"


#: Где сдвигают упор — по тому, во что упирается (`KeyCap.binding`): наш кап — на экране,
#: лимиты ключа и пространства — в самом Ahrefs.
_HOW_TO_RAISE = {
    "cap": "поднять кап — на экране «Расход»",
    "key": "поднять лимит ключа — в Ahrefs",
    "workspace": "поднять лимит пространства — в Ahrefs",
}
_UNTIL_RAISED = {
    "cap": "пока кап не поднимут на экране «Расход»",
    "key": "пока лимит ключа не поднимут в Ahrefs",
    "workspace": "пока лимит пространства не поднимут в Ahrefs",
}


async def alarm(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Тревога о капе Ahrefs — самого высокого уровня, или `None`: выбрано меньше
    `WARN_PERCENT` или размер ключа неизвестен (о неответе Ahrefs говорит своя тревога —
    «провайдер не отвечает», а экраны называют причину словами)."""
    cap = await key_cap(session, now=moment)
    percent, units_cap = cap.used_percent, cap.units_cap
    if percent is None or units_cap is None or percent < WARN_PERCENT:
        return None
    binding = cap.binding or "cap"
    if percent >= 100:
        return Alarm(
            code=REACHED_CODE,
            title="Кап Ahrefs выбран",
            detail=(
                f"Кап {spaced(units_cap)} юнитов, {_until(cap)}. Упирается {_binding(cap)}. "
                f"Новые прогоны не запустятся до обнуления или {_UNTIL_RAISED[binding]} — "
                "это не поломка, но выглядит одинаково"
            ),
            concern=cause(
                "новые прогоны не запускаются", f"ждать обнуления или {_HOW_TO_RAISE[binding]}"
            ),
        )
    warn = percent < ALARM_PERCENT
    return Alarm(
        code=WARN_CODE if warn else ALARM_CODE,
        title=f"Кап Ahrefs выбран на {WARN_PERCENT if warn else ALARM_PERCENT}% и больше",
        detail=(
            f"{'Предупреждение' if warn else 'Тревога'}: осталось {spaced(cap.left)} юнитов "
            f"из {spaced(units_cap)} — выбрано {percent}%, {_until(cap)}. Упирается "
            f"{_binding(cap)}; {_HOW_TO_RAISE[binding]}"
        ),
        concern=WARNED if warn else _short(binding),
    )


#: Пометка предупреждения: прогоны запускаются, остаток есть — повода нет.
WARNED = no_cause("прогоны запускаются: это предупреждение заранее, остаток ещё есть")


def _short(binding: str) -> Concern:
    """Пометка тревоги 95 %: остатка может не хватить следующему прогону — что сдвинуть."""
    return cause(
        "следующий прогон может не уложиться в остаток",
        f"{_HOW_TO_RAISE[binding]}, если прогоны нужны до обнуления",
    )
