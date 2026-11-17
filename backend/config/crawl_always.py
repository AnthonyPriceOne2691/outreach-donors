"""Обход 24/7: рубильник и сколько его частей идёт разом (`crawl/always.py`, 17.11.2026).

Свой модуль, а не `config/crawl.py`: обход 24/7 читают проход выдачи, обходчик и экран «Обход
доноров», и опечатка здесь не должна ронять ни один из них (`_base.spared`).

**Рубильник, а не включатель** — как `ADVERTISERS_AUTO_ROUND`. Включён по умолчанию, но сам обход
24/7 этим не начинается: его запускает человек кнопкой на вкладке «Обход доноров». Выключенный
рубильник — новых частей нет, идущие останавливаются на границе страницы и помнят, откуда
продолжить, а кнопок на экране нет.

**Сколько частей разом** — 12 из 16 обходчиков: остальные — обходам, поставленным людьми, очереди
загрузки и глубоким обходам. Части идут только в свободные обходчики и только когда очередь
обходов пуста (`always.tick`): обход, поставленный человеком, не ждёт за ними.

Опечатка в значении (`CRAWL_ALWAYS=да`) не роняет процессы, которые грузят настройку: берётся
«выключен» (или 12 частей), и при старте — громкая строка журнала с именем переменной.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared

#: Частей 24/7 разом, пока не задано иначе.
DEFAULT_SLOTS = 12


class _Always(DomainSettings):
    enabled: bool = Field(default=True, validation_alias="CRAWL_ALWAYS")
    slots: int = Field(default=DEFAULT_SLOTS, gt=0, validation_alias="CRAWL_ALWAYS_SLOTS")


_s, _unread = spared(
    _Always,
    {
        "CRAWL_ALWAYS": Fallback(
            False,
            wants="true или false",
            means="обход 24/7 не идёт, пока настройку не поправят",
        ),
        "CRAWL_ALWAYS_SLOTS": Fallback(
            DEFAULT_SLOTS,
            wants="целое число больше нуля",
            means=f"не больше {DEFAULT_SLOTS} частей обхода 24/7 разом",
        ),
    },
)

ENABLED: bool = _s.enabled
SLOTS: int = _s.slots
