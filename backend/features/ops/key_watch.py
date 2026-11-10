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

**Что сделать — в пометке, каждое число — один раз** (разбор экранов 10.11.2026: в полосе
над «Обзором» «поднять лимит ключа — в Ahrefs» стояло дважды, а «осталось N из M» и «на ключе
выбрано M − N из M» говорили одно). Пометка — строкой под заголовком полосы и последней строкой
в Telegram, подробности — по «Подробнее»: «что сделать» в них — только у предупреждения, чья
пометка его не говорит. Упор с лимитом, равным капу, назван словом (`named_binding`): его
числа — те же «осталось N из M».

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


def until_words(cap: KeyCap) -> str:
    """До какого дня — словами: обнуление ключа, а без его даты — первое число по календарю."""
    day = day_words(cap.cycle.next_reset)
    if cap.cycle.by_key:
        return f"обнуление ключа {day}"
    return f"цикл по календарю — до {day}: Ahrefs не назвал даты обнуления"


def binding_words(cap: KeyCap) -> str:
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


#: Во что упирается — словом, когда числа упора повторили бы «осталось N из M» (`named_binding`).
_BINDING_NAMES = {
    "cap": "в наш кап",
    "key": "в лимит ключа — он общий с соседней системой",
    "workspace": "в лимит пространства Ahrefs — он общий для всех его ключей",
}


def _binding_numbers(cap: KeyCap) -> tuple[int, int | None]:
    """Выбрано и из скольких у того, во что упирается, — числа `binding_words`."""
    quota = None if cap.key is None else cap.key.quota
    if cap.binding == "key" and quota is not None:
        return quota.key_used or 0, quota.key_limit
    if cap.binding == "workspace" and quota is not None:
        return quota.workspace_used or 0, quota.workspace_limit
    return cap.spent, cap.units_cap


def named_binding(cap: KeyCap) -> str:
    """Во что упирается — в тревоге, где кап и остаток уже названы числами. Числа упора те же,
    что «осталось N из M» (лимит упора равен капу), — упор назван словом; кап задан ниже лимита
    упора — у того свои числа, они и объясняют, почему осталось меньше (`binding_words`)."""
    units_cap = cap.units_cap
    used = None if units_cap is None else units_cap - cap.left
    if _binding_numbers(cap) == (used, units_cap):
        return _BINDING_NAMES[cap.binding or "cap"]
    return binding_words(cap)


#: Где сдвигают упор — по тому, во что упирается (`KeyCap.binding`): наш кап — на экране,
#: лимиты ключа и пространства — в самом Ahrefs.
HOW_TO_RAISE = {
    "cap": "поднять кап — на экране «Расход»",
    "key": "поднять лимит ключа — в Ahrefs",
    "workspace": "поднять лимит пространства — в Ahrefs",
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
                f"Кап {spaced(units_cap)} юнитов, {until_words(cap)}. Упирается "
                f"{named_binding(cap)}. Это не поломка, но выглядит одинаково"
            ),
            concern=cause(
                "новые прогоны не запускаются", f"ждать обнуления или {HOW_TO_RAISE[binding]}"
            ),
        )
    warn = percent < ALARM_PERCENT
    # Что сделать — в пометке тревоги; у предупреждения пометка его не говорит (`WARNED`).
    todo = f"; {HOW_TO_RAISE[binding]}" if warn else ""
    return Alarm(
        code=WARN_CODE if warn else ALARM_CODE,
        title=f"Кап Ahrefs выбран на {WARN_PERCENT if warn else ALARM_PERCENT}% и больше",
        detail=(
            f"{'Предупреждение' if warn else 'Тревога'}: осталось {spaced(cap.left)} юнитов "
            f"из {spaced(units_cap)} — выбрано {percent}%, {until_words(cap)}. Упирается "
            f"{named_binding(cap)}{todo}"
        ),
        concern=WARNED if warn else _short(binding),
    )


#: Пометка предупреждения: прогоны запускаются, остаток есть — повода нет.
WARNED = no_cause("прогоны запускаются: это предупреждение заранее, остаток ещё есть")


def _short(binding: str) -> Concern:
    """Пометка тревоги 95 %: остатка может не хватить следующему прогону — что сдвинуть."""
    return cause(
        "следующий прогон может не уложиться в остаток",
        f"{HOW_TO_RAISE[binding]}, если прогоны нужны до обнуления",
    )
