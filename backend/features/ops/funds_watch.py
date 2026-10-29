"""Сторож счёта выдачи: на счету DataForSEO меньше порога — «пополните счёт» (16.10.2026).

Остаток спрашивает проход сторожа вместе с «молчит ли провайдер» (`silence._serp_silent`):
запрос бесплатный и уже делается. Ответ запоминается здесь, в памяти процесса, а тревогу
считает следующий проход: лента говорит по смене кода (`ops/alarm_feed.Feed`), и минута
задержки ей не помеха. Порог — тот же, что у предохранителя прогонов (`serp/funds.py`):
сторож говорит о том же, на чём прогоны и встанут.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.ops.alarms import Alarm, cause
from backend.features.serp.funds import MIN_BALANCE_USD, dollars

#: Код тревоги: лента помнит сказанное под ним, числа в словах её не будят.
CODE = "serp-funds-low"


@dataclass
class Seen:
    """Последний остаток на счету выдачи, услышанный опросом этого процесса."""

    balance: float | None = None


#: Услышанное опросом; тесты начинают с пустого (`tests/conftest.py`).
SEEN = Seen()


def remember(balance: float) -> None:
    """Запомнить остаток из опроса провайдеров."""
    SEEN.balance = balance


def alarm() -> Alarm | None:
    """Тревога «пополните счёт», пока остаток ниже порога; неизвестен — молчим: о неответе
    провайдера говорит своя тревога."""
    balance = SEEN.balance
    if balance is None or balance >= MIN_BALANCE_USD:
        return None
    return Alarm(
        code=CODE,
        title="Пополните счёт DataForSEO",
        detail=(
            f"На счету DataForSEO {dollars(balance)} — меньше {dollars(MIN_BALANCE_USD)}: "
            "прогоны не запускаются, пока счёт не пополнят"
        ),
        concern=cause("прогоны не запускаются", "пополнить счёт DataForSEO"),
    )
