"""Режимы покупки выдачи у DataForSEO: цена против ожидания.

Проверка на проде 14.10.2026: шесть минут из одиннадцати прогон сбора продаж ждал
выдачу в отложенной очереди провайдера, а экран выглядел зависшим. Режим выбирает
человек на запуске, смета называет цену и ожидание до траты:

- `standard` — отложенная очередь: дешевле всего, ответ в среднем за пять минут;
- `priority` — та же очередь с приоритетом: вдвое дороже, около минуты;
- `live` — синхронный запрос на каждый ключ: секунды, втрое с лишним дороже.

Цена — за страницу из десяти результатов: у провайдера единица тарификации —
страница, и глубина 50 — это пять страниц (`protocol.SerpProvider.search`).
Настоящий расход берётся не отсюда, а из ответов провайдера (`provider.spent`):
эти числа нужны только смете, пока ответов ещё нет.
"""

from __future__ import annotations

from enum import StrEnum

from backend.config import serp as cfg


class SerpMode(StrEnum):
    """Как покупается выдача. Строкой — так она хранится в строке прогона."""

    STANDARD = "standard"
    PRIORITY = "priority"
    LIVE = "live"


#: Сколько ждать выдачу, в секундах, — для сметы, а не для таймаута. Средние
#: провайдера по его документации (14.10.2026); у нас отложенная очередь
#: на трёх ключах глубины 50 шла шесть минут.
EXPECTED_WAIT_S: dict[SerpMode, int] = {
    SerpMode.STANDARD: 300,
    SerpMode.PRIORITY: 60,
    SerpMode.LIVE: 10,
}


class UnknownModeError(ValueError):
    """Такого режима нет. Молча взять другой нельзя: цена у них разная втрое."""


def mode_of(value: str | None) -> SerpMode:
    """Режим из строки прогона, запроса или настройки. Пусто — умолчание
    сервиса (`SERP_MODE`); незнакомое — отказ словами."""
    raw = (value or "").strip().lower() or cfg.MODE.strip().lower()
    try:
        return SerpMode(raw)
    except ValueError:
        known = ", ".join(mode.value for mode in SerpMode)
        raise UnknownModeError(f"режима выдачи «{raw}» нет; известные: {known}") from None


def page_price_usd(mode: SerpMode) -> float:
    """Цена страницы выдачи (десять результатов) в режиме — для сметы до траты."""
    prices = {
        SerpMode.STANDARD: cfg.PRICE_PER_KEYWORD_USD,
        SerpMode.PRIORITY: cfg.PRICE_PRIORITY_USD,
        SerpMode.LIVE: cfg.PRICE_LIVE_USD,
    }
    return prices[mode]


def serp_cost_usd(keywords: int, depth_pages: int, mode: SerpMode) -> float:
    """Во что обойдётся выдача в режиме — для сметы до траты: ключи × страницы ×
    цена страницы. Одно правило у сметы прогона доноров и сбора продаж."""
    return round(max(0, keywords) * max(1, depth_pages) * page_price_usd(mode), 4)
