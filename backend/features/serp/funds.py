"""Счёт выдачи: хватит ли денег на прогон — до покупки (слово владельца 16.10.2026).

**Предохранитель, а не подсказка.** Выдачу покупают у DataForSEO деньгами со счёта, и прогон,
начатый на пустом счету, отказывает посреди платной работы, а выглядит это как «выдача ничего
не нашла» (`dataforseo.balance`). Слово владельца: на счету меньше `MIN_BALANCE_USD` —
«пополните счёт», и прогоны не запускаются; выдача по смете дороже остатка — то же. Проверяют
смета (экран называет причину и не даёт нажать), нажатие «Запустить», консольная команда и
сама задача перед покупкой: счёт мог уйти между ними. О малом остатке говорит и сторож
(`ops/funds_watch.py`).

**Остаток неизвестен — не отказ.** У провайдера нет счёта (подделка, Ahrefs как источник
выдачи) или он не ответил — проверки нет: о неответе говорит своя тревога сторожа, а покупка
у неответившего откажет громко сама.

**Названный остаток помнится** (`LAST`, 19.10.2026). Смета пересчитывается на каждую правку
формы, и сметы подряд получали от провайдера отказ вместо остатка: экран терял остаток, а
проверка счёта пропускалась. Названный за `FRESH_S` берётся без запроса, а при отказе
провайдера годится названный за `STALE_OK_S`. Память у каждого процесса своя.
Последняя проверка перед покупкой (`ensure_funds`) спрашивает провайдера всегда: память — для
смет, а не для траты, и не зависит от того, форкает ли воркер процесс на задачу.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.features.ahrefs.client import AhrefsClient
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import UnknownProviderError, build_provider
from backend.features.serp.modes import mode_of, serp_cost_usd

logger = logging.getLogger(__name__)

#: На счету меньше — прогоны не запускаются (слово владельца 16.10.2026: «меньше $5»).
MIN_BALANCE_USD = 5.0
#: Названный остаток моложе — провайдера не спрашиваем.
FRESH_S = 60.0
#: Провайдер не назвал остаток — годится названный моложе этого; старше — остаток неизвестен.
STALE_OK_S = 600.0


@dataclass(slots=True)
class Last:
    """Последний названный провайдером остаток и когда (`time.monotonic`)."""

    balance: float | None = None
    at: float = 0.0


LAST = Last()


def forget() -> None:
    """Забыть названный остаток — тестам между случаями."""
    LAST.balance, LAST.at = None, 0.0


class FundsTooLowError(RuntimeError):
    """На счету выдачи меньше порога или меньше сметы выдачи. Повтор не поможет — пополнить
    счёт может только человек (`runs/failures.is_permanent` читает признак)."""

    permanent = True


def dollars(value: float) -> str:
    """Деньги словами экрана: «4,20 $», разряды — неразрывным пробелом."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " $"


def refusal(balance: float | None, cost: float) -> str | None:
    """Почему выдачу не купить — словами; `None` — купить можно или остаток неизвестен."""
    if balance is None:
        return None
    if balance < MIN_BALANCE_USD:
        return (
            f"На счету DataForSEO {dollars(balance)} — меньше {dollars(MIN_BALANCE_USD)}: "
            "пополните счёт DataForSEO. До пополнения прогоны не запускаются."
        )
    if cost > balance:
        return (
            f"Выдача этого прогона по смете ≈ {dollars(cost)}, а на счету DataForSEO "
            f"{dollars(balance)}: пополните счёт DataForSEO."
        )
    return None


def search_cost(keywords: int, depth_pages: int, mode: str | None) -> float:
    """Смета выдачи прогона — тем же правилом, что у экрана (`serp/modes.serp_cost_usd`)."""
    return serp_cost_usd(keywords, depth_pages, mode_of(mode))


async def balance_of(provider: object, *, fresh_s: float = FRESH_S) -> float | None:
    """Остаток на счету провайдера выдачи; `None` — счёта у него нет или он не ответил, а
    названного за `STALE_OK_S` нет. Названный моложе `fresh_s` берётся без запроса; перед
    тратой — ноль: провайдера спрашивают всегда."""
    ask: Any = getattr(provider, "balance", None)
    if ask is None:
        return None
    fresh = _named_within(fresh_s)
    if fresh is not None:
        return fresh
    try:
        balance = float(await ask())
    except (SerpError, OSError) as exc:
        # Не назвал — годится названный за `STALE_OK_S`, иначе остаток неизвестен.
        named = _named_within(STALE_OK_S)
        logger.warning("счёт выдачи: остаток не узнать (%s) — %s", exc, _instead(named))
        return named
    LAST.balance, LAST.at = balance, time.monotonic()
    return balance


def _named_within(seconds: float) -> float | None:
    """Названный остаток, если он моложе `seconds`; нет такого — `None`."""
    if LAST.balance is not None and time.monotonic() - LAST.at < seconds:
        return LAST.balance
    return None


def _instead(named: float | None) -> str:
    """Что вместо остатка, которого провайдер не назвал, — словами журнала."""
    if named is None:
        return "проверка счёта пропущена"
    return f"беру названный {time.monotonic() - LAST.at:.0f} с назад"


async def provider_balance(ahrefs: AhrefsClient) -> float | None:
    """Остаток на счету источника выдачи из настроек — для сметы и нажатия «Запустить».
    Источник не настроен — `None`: о настройке запуск говорит своими словами."""
    try:
        provider = build_provider(ahrefs)
    except UnknownProviderError as exc:
        logger.warning("счёт выдачи: источник не настроен (%s)", exc)
        return None
    try:
        return await balance_of(provider)
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()


async def ensure_funds(provider: object, keywords: int, depth_pages: int, mode: str | None) -> None:
    """Перед покупкой выдачи: денег мало — `FundsTooLowError` словами, ничего не куплено.
    Остаток — у провайдера сейчас, а не из памяти смет (кросс-ревью продаж 19.10.2026)."""
    balance = await balance_of(provider, fresh_s=0.0)
    words = refusal(balance, search_cost(keywords, depth_pages, mode))
    if words is not None:
        raise FundsTooLowError(words)
