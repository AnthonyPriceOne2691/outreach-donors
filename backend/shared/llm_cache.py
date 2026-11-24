"""Кэш подсказки в расходе вызова модели (24.11.2026).

Провайдер называет в `usage.prompt_tokens_details`, сколько ввода прочитано из кэша подсказки
(`cached_tokens`) и сколько записано в него (`cache_write_tokens`, только шестое семейство).
Без этих чисел весь ввод шёл по цене ввода. У шестого семейства чтение кэша — 5–10 % цены ввода,
запись — 1,25 цены ввода, и вызов с повторяющейся подсказкой выглядел дороже, чем стоит: у судьи
сегмента на luna — примерно на 30 % (замер продаж 24.11). Ошибка была в безопасную сторону —
долларовые потолки срабатывали раньше, — но смета и отчёт врали.

Токенов и потолков кэш не меняет: `llm.tokens_of` считает весь ввод, как прежде. Цену кэша
применяет тот, кто считает деньги, — по числам отсюда.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.shared.llm import token_count


@dataclass(frozen=True, slots=True)
class CacheTokens:
    """Сколько ввода прочитано из кэша (`read`) и записано в него (`written`).

    Оба числа — **части** `prompt_tokens`, а не добавка к ним: ввод 1 588 при `cached_tokens`
    1 552 и `cache_write_tokens` 33 — это 3 токена по цене ввода, 1 552 по цене чтения кэша и
    33 по цене записи.
    """

    read: int = 0
    written: int = 0

    def uncached(self, prompt_tokens: int) -> int:
        """Сколько ввода идёт по полной цене. Не меньше нуля, даже если провайдер назвал кэша
        больше, чем ввода: отрицательный ввод уменьшил бы счёт вызова."""
        return max(prompt_tokens - self.read - self.written, 0)


def cache_tokens_of(body: dict[str, Any]) -> CacheTokens:
    """Кэш вызова из `usage.prompt_tokens_details` — тело ответа то же, что у `llm.tokens_of`.

    Нет поля, оно не целое число или в ответе нет `usage` — ноль: весь ввод тогда идёт по цене
    ввода, как было до учёта кэша. Запись в кэш называет только шестое семейство; у прежних
    моделей она ноль, а чтение (`cached_tokens`) есть и у них.
    """
    usage = body.get("usage")
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    if not isinstance(details, dict):
        return CacheTokens()
    return CacheTokens(
        read=token_count(details.get("cached_tokens")),
        written=token_count(details.get("cache_write_tokens")),
    )
